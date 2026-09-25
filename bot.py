
import asyncio
import base64
import hashlib
import hmac
import urllib.parse
import json
import logging
import os
import threading
import time
import requests
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from supabase import create_client, Client

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.constants import ParseMode
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from instagram_pipeline import processar_webhook_manus
from manus import enviar_mensagem_tarefa, listar_mensagens_tarefa

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from shopee import (
    buscar_produto_por_link,
    ShopeeAPIError,
)

from instagram_pipeline import (
    criar_lote_instagram,
    registrar_produto_processado,
    processar_lote_se_pronto,
    _extrair_attachments,
    _enviar_preview_telegram,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

TELEGRAM_ADMIN_ID = os.getenv(
    "TELEGRAM_ADMIN_ID",
    "",
).strip()

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://raposa-cacadora-roan.vercel.app/",
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "2",
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

MAX_LINKS_POR_ENVIO = 20
FILA_ORIGEM = "raposa-cacadora"
BOT_ID = os.getenv("BOT_ID", FILA_ORIGEM).strip()

# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "raposa-cacadora | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client | None = None


def iniciar_supabase():

    global supabase

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY não configurada."
        )

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    logger.info(
        "Supabase conectado."
    )


# ============================================================
# CONTROLE DO BOT
# ============================================================

bot_ativo = True

worker_task: asyncio.Task | None = None


# ============================================================
# CONFIGURAÇÕES DINÂMICAS
# ============================================================

def carregar_configuracoes_dinamicas():
    global INTERVALO_MINUTOS, bot_ativo
    try:
        if supabase is None:
            return
        row = (
            supabase.table("bot_settings")
            .select("intervalo_minutos,automacao_ativa")
            .eq("bot_id", BOT_ID)
            .limit(1)
            .execute()
        ).data
        if row:
            cfg = row[0]
            INTERVALO_MINUTOS = max(1, min(1440, int(cfg.get("intervalo_minutos") or INTERVALO_MINUTOS)))
            bot_ativo = bool(cfg.get("automacao_ativa", True))
            logger.info("Configuração dinâmica carregada: intervalo=%d, ativo=%s", INTERVALO_MINUTOS, bot_ativo)
    except Exception:
        logger.exception("Não foi possível carregar configurações dinâmicas; usando variáveis de ambiente.")


def salvar_configuracoes_dinamicas(intervalo_minutos=None, automacao_ativa=None):
    global INTERVALO_MINUTOS, bot_ativo
    intervalo = max(1, min(1440, int(intervalo_minutos if intervalo_minutos is not None else INTERVALO_MINUTOS)))
    ativo = bool(automacao_ativa if automacao_ativa is not None else bot_ativo)
    (
        supabase.table("bot_settings")
        .upsert({
            "bot_id": BOT_ID,
            "intervalo_minutos": intervalo,
            "automacao_ativa": ativo,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .execute()
    )
    INTERVALO_MINUTOS = intervalo
    bot_ativo = ativo


def registrar_auditoria(action, entity_type="system", entity_id=None, old_status=None, new_status=None, details=None):
    try:
        if supabase is None:
            return
        supabase.table("raposa_audit_log").insert({
            "bot_id": BOT_ID,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "old_status": old_status,
            "new_status": new_status,
            "details": details or {},
        }).execute()
    except Exception:
        logger.exception("Falha ao registrar auditoria: %s", action)


# ============================================================
# SERVIDOR HTTP PARA O RENDER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def _json_body(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("", "/"):
            try:
                arquivo = Path(__file__).parent / "templates" / "index.html"
                conteudo = arquivo.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(conteudo)))
                self.end_headers()
                self.wfile.write(conteudo)
            except Exception as erro:
                logger.exception("Erro ao servir Web App: %s", erro)
                self.send_response(500)
                self.end_headers()
            return

        if path == "/api/dashboard":
            try:
                init_data = self.headers.get("X-Telegram-Init-Data", "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return

                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                filtro = str(params.get("status", [""])[0] or "").strip().lower()

                consulta = (
                    supabase.table("instagram_posts")
                    .select("id,status,category,manus_task_id,manus_task_url,caption,assets,instagram_media_id,error,created_at,updated_at,published_at,bot_id")
                    .eq("bot_id", BOT_ID)
                    .order("id", desc=True)
                    .limit(50)
                )
                if filtro and filtro in {"pending","manus_processing","ready","approved","publishing","published","rejected","error"}:
                    consulta = consulta.eq("status", filtro)

                posts = consulta.execute().data or []
                counts_raw = (
                    supabase.table("instagram_posts")
                    .select("status")
                    .eq("bot_id", BOT_ID)
                    .execute()
                    .data or []
                )
                counts = {}
                for item in counts_raw:
                    estado = str(item.get("status") or "unknown")
                    counts[estado] = counts.get(estado, 0) + 1

                fila_raw = (
                    supabase.table("produtos_fila")
                    .select("status")
                    .eq("fila_origem", FILA_ORIGEM)
                    .eq("bot_id", BOT_ID)
                    .execute()
                    .data or []
                )
                fila_counts = {}
                for item in fila_raw:
                    estado = str(item.get("status") or "unknown")
                    fila_counts[estado] = fila_counts.get(estado, 0) + 1

                audit = (
                    supabase.table("raposa_audit_log")
                    .select("id,entity_type,entity_id,action,old_status,new_status,details,created_at")
                    .eq("bot_id", BOT_ID)
                    .order("id", desc=True)
                    .limit(30)
                    .execute().data or []
                )
                settings = (
                    supabase.table("bot_settings")
                    .select("intervalo_minutos,automacao_ativa,updated_at")
                    .eq("bot_id", BOT_ID)
                    .limit(1)
                    .execute().data or []
                )
                self._json_body(200, {
                    "ok": True,
                    "bot_id": BOT_ID,
                    "runtime": {"ativo": bot_ativo, "intervalo_minutos": INTERVALO_MINUTOS, "worker": bool(worker_task and not worker_task.done())},
                    "stats": {
                        "total": len(counts_raw),
                        "pending": counts.get("pending", 0) + counts.get("manus_processing", 0),
                        "ready": counts.get("ready", 0),
                        "approved": counts.get("approved", 0),
                        "published": counts.get("published", 0),
                        "rejected": counts.get("rejected", 0),
                        "error": counts.get("error", 0),
                        "fila_pendente": fila_counts.get("pending", 0),
                        "fila_processando": fila_counts.get("processing", 0),
                    },
                    "posts": posts,
                    "audit": audit,
                    "settings": settings[0] if settings else {"intervalo_minutos": INTERVALO_MINUTOS, "automacao_ativa": bot_ativo},
                })
            except Exception as erro:
                logger.exception("Erro no dashboard: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path.startswith("/api/carrossel/"):
            # /api/carrossel/{id}/imagem/{indice} serve a imagem sem expor o token do Telegram.
            partes_carrossel = [p for p in path.split("/") if p]
            if len(partes_carrossel) == 5 and partes_carrossel[0:2] == ["api", "carrossel"] and partes_carrossel[3] == "imagem":
                try:
                    init_data = self.headers.get("X-Telegram-Init-Data", "")
                    if not validar_telegram_webapp(init_data):
                        self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"}); return
                    user = extrair_usuario_webapp(init_data)
                    if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                        self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"}); return
                    post_id = int(partes_carrossel[2]); indice = int(partes_carrossel[4])
                    if indice < 0 or indice > 30: self._json_body(400, {"ok": False, "error": "indice_invalido"}); return
                    row = []
                    for tentativa in range(3):
                        try:
                            row = supabase.table("instagram_posts").select("id,bot_id,manus_task_id,assets").eq("id", post_id).eq("bot_id", BOT_ID).limit(1).execute().data
                            break
                        except Exception as exc:
                            logger.warning("Supabase indisponível ao carregar carrossel=%s tentativa=%s: %s", post_id, tentativa + 1, exc)
                            time.sleep(0.5 * (tentativa + 1))
                    if not row: self._json_body(404, {"ok": False, "error": "carrossel_nao_encontrado"}); return
                    post = row[0]; assets = post.get("assets") or []; asset = assets[indice] if isinstance(assets, list) and indice < len(assets) else None
                    file_id = asset.get("telegram_file_id") if isinstance(asset, dict) else None; image_url = None; image_bytes = None; image_content_type = None
                    if isinstance(asset, dict):
                        for key in ("image_url", "file_url", "download_url", "url"):
                            value = str(asset.get(key) or "").strip()
                            if value.startswith(("http://", "https://")): image_url = value; break
                    if file_id and TELEGRAM_TOKEN:
                        resposta = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile", params={"file_id": file_id}, timeout=20); dados = resposta.json() if resposta.ok else {}; file_path = ((dados.get("result") or {}).get("file_path") or "").strip()
                        if file_path: image_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                    if not image_url and post.get("manus_task_id"):
                        try:
                            mensagens = listar_mensagens_tarefa(str(post["manus_task_id"]))
                            # Reutiliza exatamente a mesma extração de anexos usada
                            # pelo pipeline que envia o carrossel ao Telegram.
                            anexos = _extrair_attachments({}, mensagens)
                            asset_name = str((asset or {}).get("asset_url") or "").strip().lower() if isinstance(asset, dict) else ""
                            alvo = Path(asset_name).name if asset_name else ""
                            candidatos = []
                            for item in anexos:
                                nome = str(item.get("file_name") or "").strip().lower()
                                url = str(item.get("url") or "").strip()
                                if not url:
                                    continue
                                score = 0
                                if alvo and nome == alvo:
                                    score = 100
                                elif alvo and (alvo in nome or nome in alvo):
                                    score = 90
                                elif f"slide_{indice + 1:02d}" in nome:
                                    score = 80
                                elif indice == 0 and "capa" in nome:
                                    score = 80
                                candidatos.append((score, nome, url))
                            candidatos.sort(key=lambda x: (-x[0], x[1]))
                            # Fallback posicional apenas quando não há correspondência por nome.
                            if not any(score > 0 for score, _, _ in candidatos) and indice < len(candidatos):
                                candidatos = candidatos[indice:indice + 1] + candidatos[:indice] + candidatos[indice + 1:]
                            for _, _, candidato_url in candidatos:
                                try:
                                    teste = requests.get(
                                        candidato_url,
                                        timeout=30,
                                        allow_redirects=True,
                                        headers={
                                            "User-Agent": "Mozilla/5.0 RaposaCacadora/1.0",
                                            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                                        },
                                    )
                                    content_type = (teste.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                                    if teste.ok and content_type.startswith("image/") and teste.content:
                                        image_url = candidato_url
                                        image_bytes = teste.content
                                        image_content_type = content_type
                                        logger.info("Imagem Manus recuperada: carrossel=%s slide=%s bytes=%s", post_id, indice + 1, len(image_bytes))
                                        break
                                    if teste.ok and content_type in {"text/plain", "text/html", "text/markdown", ""}:
                                        encontrados = []
                                        try:
                                            import re
                                            encontrados = re.findall(r"https?://[^\\s\"'<>]+", teste.text)
                                        except Exception:
                                            pass
                                        for url2 in encontrados:
                                            try:
                                                teste2 = requests.get(
                                                    url2,
                                                    timeout=30,
                                                    allow_redirects=True,
                                                    headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*;q=0.8"},
                                                )
                                                tipo2 = (teste2.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                                                if teste2.ok and tipo2.startswith("image/") and teste2.content:
                                                    image_url = url2
                                                    image_bytes = teste2.content
                                                    image_content_type = tipo2
                                                    logger.info("Imagem Manus recuperada por URL interna: carrossel=%s slide=%s bytes=%s", post_id, indice + 1, len(image_bytes))
                                                    break
                                            except Exception:
                                                continue
                                    if image_url:
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            logger.exception("Falha ao recuperar imagem Manus do carrossel #%s.", post_id)

                    # Último fallback: a imagem original do produto. Isso garante
                    # que o painel nunca fique sem uma prévia quando o Manus não
                    # disponibilizar mais o anexo gerado.
                    if not image_url and isinstance(asset, dict):
                        try:
                            product_id = int(asset.get("product_id") or 0)
                        except (TypeError, ValueError):
                            product_id = 0
                        if product_id:
                            try:
                                produto = (
                                    supabase.table("produtos_fila")
                                    .select("image_url")
                                    .eq("id", product_id)
                                    .eq("bot_id", BOT_ID)
                                    .limit(1)
                                    .execute()
                                ).data
                                original_url = str((produto[0].get("image_url") or "") if produto else "").strip()
                                if original_url.startswith(("http://", "https://")):
                                    teste = requests.get(original_url, timeout=30, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*;q=0.8"})
                                    tipo = (teste.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                                    if teste.ok and tipo.startswith("image/") and teste.content:
                                        image_url = original_url
                                        image_bytes = teste.content
                                        image_content_type = tipo
                                        logger.info("Imagem original do produto usada como fallback: carrossel=%s slide=%s produto=%s bytes=%s", post_id, indice + 1, product_id, len(image_bytes))
                            except Exception:
                                logger.exception("Falha no fallback da imagem original do produto #%s.", product_id)

                    if not image_bytes and image_url:
                        for tentativa in range(3):
                            try:
                                imagem = requests.get(image_url, timeout=30, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0 RaposaCacadora/1.0", "Accept": "image/*,*/*;q=0.8"})
                                tipo_final = (imagem.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                                if imagem.ok and tipo_final.startswith("image/") and imagem.content:
                                    image_bytes = imagem.content
                                    image_content_type = tipo_final
                                    break
                            except Exception as exc:
                                logger.warning("Falha no download final da imagem carrossel=%s slide=%s tentativa=%s: %s", post_id, indice + 1, tentativa + 1, exc)
                            time.sleep(0.5 * (tentativa + 1))
                    if not image_bytes:
                        logger.warning("Imagem indisponível: carrossel=%s slide=%s task=%s asset=%s", post_id, indice + 1, post.get("manus_task_id"), asset)
                        self._json_body(404, {"ok": False, "error": "imagem_nao_disponivel"}); return
                    content_type = image_content_type or "image/jpeg"
                    if not content_type.startswith("image/"): content_type = "image/jpeg"
                    self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Cache-Control", "private, max-age=300"); self.send_header("Content-Length", str(len(image_bytes))); self.end_headers(); self.wfile.write(image_bytes)
                except (ValueError, IndexError): self._json_body(400, {"ok": False, "error": "imagem_invalida"})
                except Exception as erro: logger.exception("Erro ao servir imagem do carrossel: %s", erro); self._json_body(500, {"ok": False, "error": "internal_error"})
                return

        if path.startswith("/api/carrossel/"):
            try:
                init_data = self.headers.get("X-Telegram-Init-Data", "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                raw_id = path.rsplit("/", 1)[-1]
                post_id = int(raw_id)
                post = (
                    supabase.table("instagram_posts")
                    .select("*")
                    .eq("id", post_id).eq("bot_id", BOT_ID).limit(1).execute().data
                )
                if not post:
                    self._json_body(404, {"ok": False, "error": "carrossel_nao_encontrado"})
                    return
                audit = (
                    supabase.table("raposa_audit_log")
                    .select("id,entity_type,entity_id,action,old_status,new_status,details,created_at")
                    .eq("bot_id", BOT_ID).eq("entity_type", "instagram_post").eq("entity_id", post_id)
                    .order("id", desc=False).limit(100).execute().data or []
                )
                post_data = post[0]
                product_ids = []
                for asset in (post_data.get("assets") or []):
                    if isinstance(asset, dict) and asset.get("product_id"):
                        try: product_ids.append(int(asset.get("product_id")))
                        except (TypeError, ValueError): pass
                products = []
                if product_ids:
                    products = (supabase.table("produtos_fila").select("id,product_name,link,image_url,item_id").eq("bot_id", BOT_ID).in_("id", list(dict.fromkeys(product_ids))).execute().data or [])
                self._json_body(200, {"ok": True, "post": post_data, "timeline": audit, "products": products})
            except Exception as erro:
                logger.exception("Erro no detalhe do carrossel: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/audit":
            try:
                init_data = self.headers.get("X-Telegram-Init-Data", "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                dados = (
                    supabase.table("raposa_audit_log")
                    .select("id,entity_type,entity_id,action,old_status,new_status,details,created_at")
                    .eq("bot_id", BOT_ID).order("id", desc=True).limit(100).execute().data or []
                )
                self._json_body(200, {"ok": True, "items": dados})
            except Exception:
                logger.exception("Erro na auditoria")
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/health":
            checks = {}
            try:
                supabase.table("bot_settings").select("bot_id").eq("bot_id", BOT_ID).limit(1).execute()
                checks["supabase"] = {"ok": True}
            except Exception as erro:
                checks["supabase"] = {"ok": False, "error": str(erro)[:300]}
            try:
                resposta = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=5)
                dados = resposta.json()
                checks["telegram"] = {"ok": bool(resposta.ok and dados.get("ok")), "status": resposta.status_code}
            except Exception as erro:
                checks["telegram"] = {"ok": False, "error": str(erro)[:300]}
            checks["manus"] = {"ok": bool(os.getenv("MANUS_API_KEY"))}
            checks["worker"] = {"ok": bool(worker_task and not worker_task.done()), "ativo": bot_ativo}
            self._json_body(200, {"ok": all(v.get("ok") for v in checks.values()), "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()})
            return

        if path == "/api/status":
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                raw_ids = params.get("ids", [""])[0]
                ids = [int(x) for x in raw_ids.split(",") if x.strip().isdigit()]
                if not ids:
                    self._json_body(400, {"ok": False, "error": "ids_required"})
                    return
                dados = (
                    supabase.table("produtos_fila")
                    .select("id,status")
                    .in_("id", ids)
                    .eq("fila_origem", FILA_ORIGEM)
                    .eq("bot_id", BOT_ID)
                    .execute()
                )
                dados = dados.data
                estados = {str(item["id"]): item.get("status") for item in dados}
                concluidos = sum(1 for s in estados.values() if s == "published")
                erros = sum(1 for s in estados.values() if s == "error")
                pendentes = sum(1 for s in estados.values() if s in ("pending", "processing"))
                status = "concluida" if concluidos + erros >= len(ids) else "processando"
                self._json_body(200, {
                    "ok": True, "status": status, "total": len(ids),
                    "concluidos": concluidos, "pendentes": pendentes,
                    "erros": erros, "items": estados,
                })
            except Exception as erro:
                logger.exception("Erro no status do Web App: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/carousel/action":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                dados = json.loads(self.rfile.read(length).decode("utf-8"))
                init_data = str(dados.get("initData") or self.headers.get("X-Telegram-Init-Data") or "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                post_id = int(dados.get("post_id"))
                action = str(dados.get("action") or "").strip().lower()
                post = (
                    supabase.table("instagram_posts")
                    .select("id,status,manus_task_id,manus_task_url,caption,bot_id")
                    .eq("id", post_id).eq("bot_id", BOT_ID).limit(1).execute().data
                )
                if not post:
                    self._json_body(404, {"ok": False, "error": "carrossel_nao_encontrado"})
                    return
                post = post[0]
                old = str(post.get("status") or "")
                if action in ("approve", "reject") and old != "ready":
                    self._json_body(409, {"ok": False, "error": "acao_indisponivel"})
                    return
                if action == "retry" and old != "error":
                    self._json_body(409, {"ok": False, "error": "retry_indisponivel"})
                    return
                if action == "reenviar" and old not in ("ready", "approved"):
                    self._json_body(409, {"ok": False, "error": "reenviar_indisponivel"})
                    return
                if action in ("approve", "reject"):
                    task_id = str(post.get("manus_task_id") or "").strip()
                    if not task_id:
                        self._json_body(400, {"ok": False, "error": "manus_task_missing"})
                        return
                    aprovado = action == "approve"
                    instrucao = (
                        f"O carrossel #{post_id} foi APROVADO pelo administrador no painel. Continue o fluxo e publique no Instagram conforme as instruções originais."
                        if aprovado else
                        f"O carrossel #{post_id} foi REPROVADO pelo administrador no painel. Não publique este carrossel e encerre o fluxo."
                    )
                    await_result = enviar_mensagem_tarefa(task_id, instrucao)
                    novo = "approved" if aprovado else "rejected"
                    supabase.table("instagram_posts").update({
                        "status": novo,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
                    registrar_auditoria("panel_approve" if aprovado else "panel_reject","instagram_post",post_id,old,novo,{"telegram_user_id":user.get("id")})
                    self._json_body(200, {"ok": True, "status": novo, "message": "Decisão enviada ao Manus."})
                    return
                if action == "retry":
                    supabase.table("instagram_posts").update({
                        "status": "pending", "error": None, "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
                    registrar_auditoria("panel_retry","instagram_post",post_id,old,"pending",{"telegram_user_id":user.get("id")})
                    self._json_body(200, {"ok": True, "status": "pending", "message": "Carrossel devolvido para processamento."})
                    return
                if action == "reenviar":
                    task_id = str(post.get("manus_task_id") or "").strip()
                    if not task_id:
                        self._json_body(400, {"ok": False, "error": "manus_task_missing"})
                        return
                    from manus import listar_mensagens_tarefa
                    mensagens = listar_mensagens_tarefa(task_id)
                    attachments = _extrair_attachments({}, mensagens)
                    if not attachments:
                        self._json_body(400, {"ok": False, "error": "attachments_missing"})
                        return
                    enviado = _enviar_preview_telegram(post_id, {}, attachments, str(post.get("caption") or ""), str(post.get("manus_task_url") or "") or None)
                    if not enviado:
                        self._json_body(500, {"ok": False, "error": "reenviar_falhou"})
                        return
                    registrar_auditoria("panel_reenviar","instagram_post",post_id,old,old,{"telegram_user_id":user.get("id")})
                    self._json_body(200, {"ok": True, "message": "Carrossel reenviado para o Telegram."})
                    return
                self._json_body(400, {"ok": False, "error": "acao_invalida"})
            except Exception as erro:
                logger.exception("Erro na ação do painel: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/settings":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                dados = json.loads(self.rfile.read(length).decode("utf-8"))
                init_data = str(dados.get("initData") or self.headers.get("X-Telegram-Init-Data") or "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                intervalo = int(dados.get("intervalo_minutos") or INTERVALO_MINUTOS)
                if intervalo < 1 or intervalo > 1440:
                    self._json_body(400, {"ok": False, "error": "intervalo_invalido"})
                    return
                ativo = bool(dados.get("automacao_ativa"))
                salvar_configuracoes_dinamicas(intervalo, ativo)
                registrar_auditoria("settings_update","system",None,None,None,{"intervalo_minutos":intervalo,"automacao_ativa":ativo,"telegram_user_id":user.get("id")})
                self._json_body(200, {"ok": True, "settings": {"intervalo_minutos":INTERVALO_MINUTOS,"automacao_ativa":bot_ativo}})
            except Exception:
                logger.exception("Erro ao salvar configurações")
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/control":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                dados = json.loads(self.rfile.read(length).decode("utf-8"))
                init_data = str(dados.get("initData") or self.headers.get("X-Telegram-Init-Data") or "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                ativo = bool(dados.get("ativo"))
                salvar_configuracoes_dinamicas(None, ativo)
                registrar_auditoria("control_start" if ativo else "control_stop","system",None,None,None,{"telegram_user_id":user.get("id")})
                self._json_body(200, {"ok": True, "ativo":bot_ativo})
            except Exception:
                logger.exception("Erro no controle do bot")
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/webhook/manus":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Raposa Cacadora OK")
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/webhook/manus":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            signature = self.headers.get("X-Webhook-Signature", "")
            timestamp = self.headers.get("X-Webhook-Timestamp", "")
            if not verificar_assinatura_manus(body, signature, timestamp,
                                              f"https://{self.headers.get('Host', '')}{self.path}"):
                self._json_body(401, {"ok": False, "error": "invalid_signature"})
                return
            try:
                payload = json.loads(body.decode("utf-8"))
                ok, message = processar_webhook_manus(supabase, payload)
                self._json_body(200 if ok else 500, {"ok": ok, "message": message})
            except Exception as erro:
                logger.exception("Erro no webhook Manus: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        if path == "/api/configurar":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                dados = json.loads(body.decode("utf-8"))
                init_data = str(dados.get("initData") or "")
                if not validar_telegram_webapp(init_data):
                    self._json_body(401, {"ok": False, "error": "telegram_auth_invalid"})
                    return
                user = extrair_usuario_webapp(init_data)
                if not user or int(user.get("id", 0)) != int(TELEGRAM_ADMIN_ID):
                    self._json_body(403, {"ok": False, "error": "usuario_nao_autorizado"})
                    return
                links = dados.get("links") or []
                links = extrair_links(" ".join(str(link) for link in links))
                if not links:
                    self._json_body(400, {"ok": False, "error": "nenhum_link_shopee"})
                    return
                if len(links) > MAX_LINKS_POR_ENVIO:
                    self._json_body(400, {"ok": False, "error": "limite_20_links"})
                    return
                adicionados, duplicados, erros, ids = inserir_links(links)
                if ids and len(ids) >= 2:
                    try:
                        criar_lote_instagram(supabase, ids, str(user["id"]), None)
                    except Exception:
                        logger.exception("Erro ao criar lote Instagram via Web App.")
                self._json_body(200, {
                    "ok": True,
                    "mensagem": f"{adicionados} produto(s) adicionado(s) à fila.",
                    "task_id": ",".join(str(x) for x in ids),
                    "ids": ids, "adicionados": adicionados,
                    "duplicados": duplicados, "erros": len(erros),
                })
            except Exception as erro:
                logger.exception("Erro no /api/configurar: %s", erro)
                self._json_body(500, {"ok": False, "error": "internal_error"})
            return

        self._json_body(404, {"ok": False, "error": "not_found"})

    def log_message(self, format, *args):
        return


MANUS_PUBLIC_KEY_CACHE = ""
MANUS_PUBLIC_KEY_CACHE_AT = 0.0
MANUS_PUBLIC_KEY_CACHE_TTL = 3600


def obter_chave_publica_manus():
    global MANUS_PUBLIC_KEY_CACHE
    global MANUS_PUBLIC_KEY_CACHE_AT

    configured_key = os.getenv(
        "MANUS_WEBHOOK_PUBLIC_KEY",
        "",
    ).strip()

    if configured_key:
        if "\\n" in configured_key:
            configured_key = configured_key.replace("\\n", "\n")
        return configured_key

    agora = time.time()

    if (
        MANUS_PUBLIC_KEY_CACHE
        and agora - MANUS_PUBLIC_KEY_CACHE_AT < MANUS_PUBLIC_KEY_CACHE_TTL
    ):
        return MANUS_PUBLIC_KEY_CACHE

    api_url = os.getenv(
        "MANUS_API_URL",
        "https://api.manus.ai",
    ).rstrip("/")
    api_key = os.getenv(
        "MANUS_API_KEY",
        "",
    ).strip()

    if not api_key:
        logger.error(
            "MANUS_API_KEY não configurada; não é possível obter a chave pública."
        )
        return ""

    try:
        resposta = requests.get(
            f"{api_url}/v2/webhook.publicKey",
            headers={"x-manus-api-key": api_key},
            timeout=15,
        )
        resposta.raise_for_status()

        dados = resposta.json()
        public_key = str(
            dados.get("public_key", "")
        ).strip()

        if not public_key:
            raise RuntimeError(
                "Manus não retornou public_key."
            )

        if "\\n" in public_key:
            public_key = public_key.replace("\\n", "\n")

        MANUS_PUBLIC_KEY_CACHE = public_key
        MANUS_PUBLIC_KEY_CACHE_AT = agora

        logger.info(
            "Chave pública do Manus obtida e armazenada em cache."
        )

        return public_key

    except Exception as erro:
        logger.exception(
            "Erro ao obter chave pública do Manus: %s",
            erro,
        )
        return ""


def verificar_assinatura_manus(
    body,
    signature,
    timestamp,
    request_url,
):
    public_key = obter_chave_publica_manus()

    if not public_key or not signature or not timestamp:
        return False

    try:
        ts = int(timestamp)
        if abs(int(time.time()) - ts) > 300:
            return False

        body_hash = hashlib.sha256(body).hexdigest()
        signed_content = (
            f"{timestamp}.{request_url}.{body_hash}"
        ).encode("utf-8")

        key = serialization.load_pem_public_key(
            public_key.encode("utf-8")
        )
        key.verify(
            base64.b64decode(signature),
            signed_content,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        logger.exception("Assinatura do webhook Manus inválida.")
        return False


def iniciar_servidor_http():

    try:

        servidor = ThreadingHTTPServer(
            (
                "0.0.0.0",
                PORT,
            ),
            HealthHandler,
        )

        logger.info(
            "Servidor HTTP iniciado em 0.0.0.0:%d",
            PORT,
        )

        servidor.serve_forever()

    except Exception as erro:

        logger.exception(
            "Erro no servidor HTTP: %s",
            erro,
        )


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def validar_configuracao():

    erros = []

    if not TELEGRAM_TOKEN:
        erros.append(
            "TELEGRAM_TOKEN não configurado."
        )

    if not TELEGRAM_CHAT_ID:
        erros.append(
            "TELEGRAM_CHAT_ID não configurado."
        )

    if not TELEGRAM_ADMIN_ID:
        erros.append(
            "TELEGRAM_ADMIN_ID não configurado."
        )

    if not SUPABASE_URL:
        erros.append(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        erros.append(
            "SUPABASE_KEY não configurada."
        )

    if INTERVALO_MINUTOS < 1:
        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0."
        )

    if erros:

        for erro in erros:
            logger.error(erro)

        raise RuntimeError(
            "Configuração inválida."
        )

    try:

        int(TELEGRAM_ADMIN_ID)

    except ValueError:

        raise RuntimeError(
            "TELEGRAM_ADMIN_ID deve ser numérico."
        )

    logger.info(
        "Configuração validada."
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS,
    )

    logger.info(
        "Porta HTTP: %d",
        PORT,
    )


# ============================================================
# TELEGRAM WEB APP
# ============================================================

def validar_telegram_webapp(init_data: str) -> bool:
    if not init_data or not TELEGRAM_TOKEN:
        return False
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        recebido = params.pop("hash", "")
        if not recebido:
            return False
        data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret_key = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calculado = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(calculado, recebido)
    except Exception:
        logger.exception("Erro validando Telegram Web App.")
        return False


def extrair_usuario_webapp(init_data: str) -> dict | None:
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        usuario = params.get("user")
        return json.loads(usuario) if usuario else None
    except Exception:
        return None


# ============================================================
# AUTORIZAÇÃO
# ============================================================

def usuario_autorizado(
    update: Update,
) -> bool:

    if not update.effective_user:
        return False

    try:

        admin_id = int(
            TELEGRAM_ADMIN_ID
        )

    except ValueError:

        return False

    return (
        update.effective_user.id
        == admin_id
    )


# ============================================================
# EXTRAIR LINKS
# ============================================================

def extrair_links(
    texto: str,
) -> list[str]:

    if not texto:
        return []

    links = []

    for parte in texto.split():

        link = (
            parte
            .strip()
            .strip("<>")
            .strip()
        )

        while link and link[-1] in (
            ",",
            ".",
            ";",
            ")",
            "]",
            "}",
        ):
            link = link[:-1]

        if not (
            link.startswith("http://")
            or link.startswith("https://")
        ):
            continue

        link_lower = link.lower()

        if (
            "shopee.com.br" in link_lower
            or "s.shopee.com.br" in link_lower
        ):
            links.append(link)

    resultado = []
    vistos = set()

    for link in links:

        chave = link.strip()

        if chave not in vistos:

            vistos.add(chave)
            resultado.append(chave)

    return resultado


# ============================================================
# SUPABASE - INSERIR LINKS
# ============================================================

def inserir_links(
    links: list[str],
) -> tuple[int, int, list[str]]:

    if supabase is None:
        raise RuntimeError(
            "Supabase não inicializado."
        )

    adicionados = 0
    duplicados = 0
    erros = []
    ids_inseridos = []

    for link in links:

        try:

            resposta = (
                supabase
                .table("produtos_fila")
                .insert(
                    {
                        "link": link,
                        "status": "pending",
                        "fila_origem": FILA_ORIGEM,
                        "bot_id": BOT_ID,
                    }
                )
                .execute()
            )

            if resposta.data:
                adicionados += 1
                ids_inseridos.append(int(resposta.data[0]["id"]))

        except Exception as erro:

            mensagem_erro = str(
                erro
            )

            texto = mensagem_erro.lower()

            if (
                "duplicate" in texto
                or "unique" in texto
                or "23505" in mensagem_erro
            ):

                duplicados += 1

                logger.info(
                    "Link já existente: %s",
                    link,
                )

            else:

                logger.exception(
                    "Erro ao inserir link: %s",
                    link,
                )

                erros.append(link)

    return (
        adicionados,
        duplicados,
        erros,
        ids_inseridos,
    )


# ============================================================
# SUPABASE - BUSCAR PRÓXIMO PRODUTO
# ============================================================

def buscar_proximo_produto():

    if supabase is None:
        raise RuntimeError(
            "Supabase não inicializado."
        )

    resposta = (
        supabase
        .table("produtos_fila")
        .select("*")
        .eq("status", "pending")
        .eq("fila_origem", FILA_ORIGEM)
        .eq("bot_id", BOT_ID)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if not resposta.data:
        return None

    return resposta.data[0]


# ============================================================
# SUPABASE - MARCAR PROCESSANDO
# ============================================================

def marcar_processando(
    produto_id: int,
) -> bool:

    if supabase is None:
        return False

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "processing",
                "processing_at": agora,
            }
        )
        .eq("id", produto_id)
        .eq("status", "pending")
        .execute()
    )

    return bool(
        resposta.data
    )


# ============================================================
# SUPABASE - MARCAR PUBLICADO
# ============================================================

def marcar_publicado(
    produto_id: int,
    produto: dict[str, Any],
    telegram_message_id: int | None,
):

    if supabase is None:
        return

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    dados = {
        "status": "published",
        "product_name": (
            produto.get("productName")
            or "Produto"
        ),
        "shop_id": produto.get(
            "shopId"
        ),
        "item_id": produto.get(
            "itemId"
        ),
        "image_url": produto.get(
            "imageUrl"
        ),
        "telegram_message_id": (
            telegram_message_id
        ),
        "telegram_chat_id": (
            TELEGRAM_CHAT_ID
        ),
        "published_at": agora,
        "processing_at": None,
        "erro": None,
    }

    (
        supabase
        .table("produtos_fila")
        .update(dados)
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - MARCAR ERRO
# ============================================================

def marcar_erro(
    produto_id: int,
    erro: str,
):

    if supabase is None:
        return

    resposta = (
        supabase
        .table("produtos_fila")
        .select("tentativas")
        .eq("id", produto_id)
        .limit(1)
        .execute()
    )

    tentativas = 0

    if resposta.data:

        tentativas = int(
            resposta.data[0].get(
                "tentativas",
                0,
            )
            or 0
        )

    tentativas += 1

    (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "error",
                "tentativas": tentativas,
                "erro": erro[:2000],
                "processing_at": None,
            }
        )
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - RECUPERAR PROCESSAMENTOS PRESOS
# ============================================================

def recuperar_processamentos_presos():

    if supabase is None:
        return

    limite = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            minutes=max(
                INTERVALO_MINUTOS * 2,
                20,
            )
        )
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .select("id")
        .eq("status", "processing")
        .eq("fila_origem", FILA_ORIGEM)
        .eq("bot_id", BOT_ID)
        .lt("processing_at", limite)
        .execute()
    )

    if not resposta.data:
        return

    for produto in resposta.data:

        produto_id = produto["id"]

        (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "processing_at": None,
                }
            )
            .eq("id", produto_id)
            .eq("fila_origem", FILA_ORIGEM)
            .eq("bot_id", BOT_ID)
            .eq("status", "processing")
            .execute()
        )

        logger.warning(
            "Produto %s recuperado de processing.",
            produto_id,
        )


# ============================================================
# NÚMEROS
# ============================================================

def numero(
    valor,
    padrao=0.0,
):

    try:

        if valor is None:
            return padrao

        texto = str(
            valor
        ).strip()

        if not texto:
            return padrao

        return float(
            texto.replace(
                ",",
                ".",
            )
        )

    except Exception:

        return padrao


def inteiro(
    valor,
    padrao=0,
):

    try:

        if valor is None:
            return padrao

        return int(
            float(valor)
        )

    except Exception:

        return padrao


# ============================================================
# MOEDA
# ============================================================

def moeda(
    valor,
):

    valor = numero(
        valor
    )

    texto = (
        f"{valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {texto}"


# ============================================================
# VENDAS
# ============================================================

def formatar_vendas(
    vendas,
):

    vendas = inteiro(
        vendas
    )

    return (
        f"{vendas:,}"
        .replace(",", ".")
    )


# ============================================================
# MENSAGEM DO PRODUTO
# ============================================================

def montar_mensagem(
    produto,
):

    nome = (
        produto.get(
            "productName"
        )
        or "Produto"
    )

    preco = numero(
        produto.get(
            "price"
        )
    )

    preco_min = numero(
        produto.get(
            "priceMin"
        )
    )

    desconto = numero(
        produto.get(
            "priceDiscountRate"
        )
    )

    avaliacao = numero(
        produto.get(
            "ratingStar"
        )
    )

    vendas = inteiro(
        produto.get(
            "sales"
        )
    )

    loja = (
        produto.get(
            "shopName"
        )
        or "Loja Shopee"
    )

    preco_atual = preco

    if preco_min > 0:
        preco_atual = preco_min

    if (
        desconto > 0
        and desconto < 100
        and preco_atual > 0
    ):

        preco_anterior = (
            preco_atual
            / (
                1
                - desconto / 100
            )
        )

    else:

        preco_anterior = preco_atual

    mensagem = (
        "🔥 <b>OFERTA EM DESTAQUE</b>\n"
        "\n"
        f"✨ <b>{nome}</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"❌ De: <s>{moeda(preco_anterior)}</s>\n"
        f"💰 <b>Por apenas: {moeda(preco_atual)}</b>\n"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"⭐ <b>{avaliacao:.1f}</b>/5 de avaliação\n"
        f"📦 <b>{formatar_vendas(vendas)}</b> vendas\n"
        f"🏪 <b>{loja}</b>\n"
        "\n"
        "🚨 <b>Preço sujeito a alteração.</b>\n"
        "⚡ Aproveite enquanto estiver disponível!"
    )

    return mensagem


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str,
):

    mensagem = montar_mensagem(
        produto
    )

    image_url = (
        produto.get(
            "imageUrl"
        )
        or ""
    )

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 COMPRAR AGORA",
                    url=link_afiliado,
                )
            ]
        ]
    )

    if image_url:

        try:

            mensagem_enviada = (
                await bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=image_url,
                    caption=mensagem,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado,
                )
            )

            logger.info(
                "Produto publicado com imagem e botão."
            )

            return (
                True,
                mensagem_enviada.message_id,
            )

        except Exception as erro:

            logger.warning(
                "Falha ao enviar imagem: %s",
                erro,
            )

    try:

        mensagem_enviada = (
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
                disable_web_page_preview=True,
            )
        )

        logger.info(
            "Produto publicado somente como texto."
        )

        return (
            True,
            mensagem_enviada.message_id,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao publicar produto: %s",
            erro,
        )

        return (
            False,
            None,
        )


# ============================================================
# NOTIFICAÇÃO PARA ADMIN
# ============================================================

async def enviar_notificacao_admin(
    bot: Bot,
    texto: str,
):

    try:

        await bot.send_message(
            chat_id=int(
                TELEGRAM_ADMIN_ID
            ),
            text=texto,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception as erro:

        logger.warning(
            "Não foi possível notificar admin: %s",
            erro,
        )


# ============================================================
# PROCESSAR PRODUTO
# ============================================================

async def processar_produto(
    bot: Bot,
    produto_fila: dict[str, Any],
):

    produto_id = produto_fila["id"]
    link = produto_fila["link"]

    logger.info(
        "=========================================="
    )

    logger.info(
        "Processando produto ID %s",
        produto_id,
    )

    logger.info(
        "Link: %s",
        link,
    )

    reservado = await asyncio.to_thread(
        marcar_processando,
        produto_id,
    )

    if not reservado:

        logger.info(
            "Produto %s não pôde ser reservado.",
            produto_id,
        )

        return False

    try:

        produto = await asyncio.to_thread(
            buscar_produto_por_link,
            link,
        )

        if not produto:

            raise ShopeeAPIError(
                "Produto não encontrado."
            )

        logger.info(
            "Produto encontrado: %s",
            produto.get(
                "productName",
                "Produto",
            ),
        )

        sucesso, message_id = (
            await publicar_produto(
                bot=bot,
                produto=produto,
                link_afiliado=link,
            )
        )

        if not sucesso:

            raise RuntimeError(
                "Falha ao publicar no Telegram."
            )

        await asyncio.to_thread(
            marcar_publicado,
            produto_id,
            produto,
            message_id,
        )

        try:
            post_ids = await asyncio.to_thread(
                registrar_produto_processado,
                supabase,
                produto_id,
                produto,
            )
            for post_id in post_ids:
                await asyncio.to_thread(
                    processar_lote_se_pronto,
                    supabase,
                    post_id,
                )
        except Exception:
            logger.exception("Erro ao atualizar pipeline Instagram.")

        logger.info(
            "Produto %s marcado como publicado.",
            produto_id,
        )

        await enviar_notificacao_admin(
            bot,
            (
                "✅ <b>PRODUTO PUBLICADO</b>\n"
                "\n"
                f"📦 <b>{produto.get('productName', 'Produto')}</b>\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"📨 Mensagem: <b>#{message_id}</b>"
            ),
        )

        return True

    except ShopeeAPIError as erro:

        logger.error(
            "Erro da Shopee: %s",
            erro,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"🔗 {link}\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False

    except Exception as erro:

        logger.exception(
            "Erro ao processar produto %s",
            produto_id,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"🔗 {link}\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False


# ============================================================
# TECLADO PRINCIPAL
# ============================================================

def teclado_controle():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 ABRIR PAINEL",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            ],
            [
                InlineKeyboardButton(
                    "▶️ INICIAR",
                    callback_data="bot_iniciar",
                ),
                InlineKeyboardButton(
                    "⏹️ STOP",
                    callback_data="bot_stop",
                ),
            ],
        ]
    )


# ============================================================
# /START
# ============================================================

async def comando_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        "Envie um ou vários links da Shopee "
        "para colocar na fila.\n"
        "\n"
        f"📦 Máximo por envio: <b>{MAX_LINKS_POR_ENVIO}</b>\n"
        f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n"
        "\n"
        "O bot salva tudo no Supabase, então "
        "a fila continua mesmo se o Render reiniciar.\n"
        "\n"
        "Use os botões abaixo para controlar a publicação."
    )

    await update.message.reply_text(
        mensagem,
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /STATUS
# ============================================================

async def comando_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select("status")
            .eq("fila_origem", FILA_ORIGEM)
            .eq("bot_id", BOT_ID)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        total = len(
            registros
        )

        publicados = sum(
            1
            for item in registros
            if item.get("status")
            == "published"
        )

        pendentes = sum(
            1
            for item in registros
            if item.get("status")
            == "pending"
        )

        processando = sum(
            1
            for item in registros
            if item.get("status")
            == "processing"
        )

        erros = sum(
            1
            for item in registros
            if item.get("status")
            == "error"
        )

        estado = (
            "🟢 ATIVO"
            if bot_ativo
            else "🔴 PARADO"
        )

        mensagem = (
            "🦊 <b>RAPOSA CAÇADORA</b>\n"
            "\n"
            "📊 <b>STATUS DA FILA</b>\n"
            "\n"
            f"🤖 Bot: <b>{estado}</b>\n"
            f"📦 Total: <b>{total}</b>\n"
            f"✅ Publicados: <b>{publicados}</b>\n"
            f"⏳ Aguardando: <b>{pendentes}</b>\n"
            f"🔄 Processando: <b>{processando}</b>\n"
            f"❌ Erros: <b>{erros}</b>\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /status"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar status:\n{erro}"
        )


# ============================================================
# /FILA
# ============================================================

async def comando_fila(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,product_name,status,created_at"
            )
            .eq("fila_origem", FILA_ORIGEM)
            .eq("bot_id", BOT_ID)
            .order("id", desc=False)
            .limit(100)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "🦊 A fila está vazia."
            )

            return

        linhas = [
            "🦊 <b>FILA DE PRODUTOS</b>",
            "",
        ]

        simbolos = {
            "pending": "⏳",
            "processing": "🔄",
            "published": "✅",
            "error": "❌",
        }

        for item in registros:

            simbolo = simbolos.get(
                item.get("status"),
                "❓",
            )

            nome = (
                item.get(
                    "product_name"
                )
                or "Aguardando processamento"
            )

            if len(nome) > 45:

                nome = (
                    nome[:42]
                    + "..."
                )

            linhas.append(
                f"{simbolo} #{item['id']} — {nome}"
            )

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /fila"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar fila:\n{erro}"
        )


# ============================================================
# /ERROS
# ============================================================

async def comando_erros(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,erro,tentativas"
            )
            .eq("fila_origem", FILA_ORIGEM)
            .eq("bot_id", BOT_ID)
            .eq("status", "error")
            .order("id", desc=False)
            .limit(20)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "✅ Não existem produtos com erro."
            )

            return

        linhas = [
            "⚠️ <b>PRODUTOS COM ERRO</b>",
            "",
        ]

        for item in registros:

            erro = (
                item.get("erro")
                or "Erro desconhecido"
            )

            if len(erro) > 300:

                erro = (
                    erro[:297]
                    + "..."
                )

            linhas.append(
                f"❌ <b>#{item['id']}</b>"
            )

            linhas.append(
                f"Tentativas: {item.get('tentativas', 0)}"
            )

            linhas.append(
                f"Erro: {erro}"
            )

            linhas.append("")

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /erros"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar erros:\n{erro}"
        )


# ============================================================
# /RETRY
# ============================================================

async def comando_retry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "erro": None,
                    "processing_at": None,
                }
            )
            .eq("fila_origem", FILA_ORIGEM)
            .eq("bot_id", BOT_ID)
            .eq("status", "error")
            .execute()
        )

        quantidade = len(
            resposta.data
            or []
        )

        await update.message.reply_text(
            (
                "🔄 <b>REPROCESSAMENTO</b>\n"
                "\n"
                f"✅ {quantidade} produto(s) "
                "voltaram para a fila.\n"
                "\n"
                "Use ▶️ INICIAR para liberar a publicação."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /retry"
        )

        await update.message.reply_text(
            f"❌ Erro ao reprocessar:\n{erro}"
        )


# ============================================================
# /REENVIAR_ULTIMO
# ============================================================

async def comando_reenviar_ultimo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        await update.message.reply_text("❌ Supabase não está conectado.")
        return

    try:
        resposta = (
            supabase
            .table("instagram_posts")
            .select("id,status,manus_task_id,manus_task_url,caption,bot_id")
            .eq("status", "ready")
            .eq("bot_id", BOT_ID)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )

        if not resposta.data:
            await update.message.reply_text("⚠️ Não existe nenhum carrossel pronto para reenviar.")
            return

        post = resposta.data[0]
        post_id = int(post["id"])
        task_id = str(post.get("manus_task_id") or "").strip()

        if not task_id:
            await update.message.reply_text(f"⚠️ O carrossel #{post_id} não possui tarefa Manus vinculada.")
            return

        from manus import listar_mensagens_tarefa
        mensagens = await asyncio.to_thread(listar_mensagens_tarefa, task_id)
        attachments = _extrair_attachments({}, mensagens)

        if not attachments:
            await update.message.reply_text(
                f"❌ Não encontrei as imagens do carrossel #{post_id} na tarefa Manus.\n\n"
                "Os anexos podem ter expirado ou não estar disponíveis."
            )
            return

        enviado = await asyncio.to_thread(
            _enviar_preview_telegram,
            post_id,
            {},
            attachments,
            str(post.get("caption") or ""),
            str(post.get("manus_task_url") or "") or None,
        )

        if enviado:
            await update.message.reply_text(
                f"✅ Carrossel #{post_id} reenviado para o Telegram com os botões APROVAR/REPROVAR."
            )
        else:
            await update.message.reply_text(
                f"❌ Não foi possível reenviar o carrossel #{post_id}."
            )

    except Exception as erro:
        logger.exception("Erro no comando /reenviar_ultimo: %s", erro)
        await update.message.reply_text(
            f"❌ Erro ao reenviar o último carrossel:\n{str(erro)[:1500]}"
        )


# ============================================================
# /STOP
# ============================================================

async def comando_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    bot_ativo = False
    try: salvar_configuracoes_dinamicas(None, False)
    except Exception: logger.exception("Falha ao persistir STOP")

    logger.warning(
        "Publicação pausada pelo administrador."
    )

    await update.message.reply_text(
        (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "Nenhum novo produto será publicado.\n"
            "Os produtos que já estão no Supabase "
            "continuam na fila.\n"
            "\n"
            "▶️ Use /iniciar ou o botão INICIAR "
            "para continuar."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /INICIAR
# ============================================================

async def comando_iniciar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    bot_ativo = True
    try: salvar_configuracoes_dinamicas(None, True)
    except Exception: logger.exception("Falha ao persistir INICIAR")

    logger.info(
        "Publicação iniciada pelo administrador."
    )

    await update.message.reply_text(
        (
            "▶️ <b>PUBLICAÇÃO INICIADA</b>\n"
            "\n"
            "A Raposa Caçadora voltou a processar "
            "a fila do Supabase.\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )
    
# ============================================================
# BOTÕES
# ============================================================

async def callback_controle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    query = update.callback_query

    if query is None:
        return

    if not usuario_autorizado(update):

        await query.answer(
            "Você não está autorizado.",
            show_alert=True,
        )

        return

    await query.answer()

    if query.data and query.data.startswith(("carousel_approve:", "carousel_reject:")):
        try:
            acao, raw_post_id = query.data.split(":", 1)
            post_id = int(raw_post_id)
        except (ValueError, AttributeError):
            await query.answer("Botão inválido.", show_alert=True)
            return

        if supabase is None:
            await query.answer("Supabase não está conectado.", show_alert=True)
            return

        try:
            resultado = (
                supabase.table("instagram_posts")
                .select("id,manus_task_id,status,bot_id")
                .eq("id", post_id)
                .eq("bot_id", BOT_ID)
                .limit(1)
                .execute()
            )
            if not resultado.data:
                await query.answer("Carrossel não encontrado.", show_alert=True)
                return

            post = resultado.data[0]
            task_id = str(post.get("manus_task_id") or "").strip()
            if not task_id:
                await query.answer("A tarefa Manus não está vinculada a este carrossel.", show_alert=True)
                return

            status_atual = str(post.get("status") or "").strip().lower()
            if status_atual in {"approved", "rejected", "published"}:
                await query.answer(f"Este carrossel já está com status: {status_atual}.", show_alert=True)
                return

            aprovado = acao == "carousel_approve"
            decisao = "APROVADO" if aprovado else "REPROVADO"
            novo_status = "approved" if aprovado else "rejected"
            instrucao = (
                f"O carrossel #{post_id} foi APROVADO pelo administrador no Telegram. "
                "Continue o fluxo e publique o carrossel no Instagram conforme as instruções originais."
                if aprovado
                else
                f"O carrossel #{post_id} foi REPROVADO pelo administrador no Telegram. "
                "Não publique este carrossel. Encerre o fluxo de publicação desta tarefa."
            )

            await asyncio.to_thread(enviar_mensagem_tarefa, task_id, instrucao)

            supabase.table("instagram_posts").update({
                "status": novo_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", post_id).eq("bot_id", BOT_ID).execute()

            logger.info(
                "Decisão %s enviada à tarefa Manus %s para o lote #%s.",
                decisao,
                task_id,
                post_id,
            )

            texto = (
                f"🦊 <b>CARROSSEL #{post_id} — {decisao}</b>\\n\\n"
                "A decisão foi enviada ao Manus. "
                + ("O Manus continuará o fluxo de publicação." if aprovado else "O Manus foi instruído a não publicar.")
            )
            try:
                await query.edit_message_reply_markup(reply_markup=None)
                if query.message:
                    await query.message.reply_text(texto, parse_mode=ParseMode.HTML)
            except Exception:
                if query.message:
                    await query.message.reply_text(texto, parse_mode=ParseMode.HTML)

        except Exception as erro:
            logger.exception("Erro ao enviar decisão do carrossel #%s para Manus: %s", post_id, erro)
            await query.answer("Não foi possível enviar a decisão ao Manus.", show_alert=True)

        return

    if query.data == "bot_stop":

        bot_ativo = False
        try: salvar_configuracoes_dinamicas(None, False)
        except Exception: logger.exception("Falha ao persistir STOP do botão")

        logger.warning(
            "Publicação parada pelo botão STOP."
        )

        texto = (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "A fila permanece salva no Supabase.\n"
            "Nenhum novo produto será publicado.\n"
            "\n"
            "▶️ Pressione INICIAR para continuar."
        )

    elif query.data == "bot_iniciar":

        bot_ativo = True
        try: salvar_configuracoes_dinamicas(None, True)
        except Exception: logger.exception("Falha ao persistir INICIAR do botão")

        logger.info(
            "Publicação iniciada pelo botão."
        )

        texto = (
            "▶️ <b>PUBLICAÇÃO INICIADA</b>\n"
            "\n"
            "A Raposa voltou a processar a fila.\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

    else:

        return

    try:

        await query.edit_message_text(
            texto,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception:

        try:

            if query.message:

                await query.message.reply_text(
                    texto,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado_controle(),
                )

        except Exception:

            pass


# ============================================================
# RECEBER LINKS PELO TELEGRAM
# ============================================================

async def receber_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    texto = (
        update.message.text
        or update.message.caption
        or ""
    )

    links = extrair_links(
        texto
    )

    if not links:

        await update.message.reply_text(
            (
                "⚠️ Não encontrei links da Shopee.\n\n"
                "Envie um ou vários links, por exemplo:\n"
                "https://s.shopee.com.br/..."
            )
        )

        return

    if len(links) > MAX_LINKS_POR_ENVIO:

        await update.message.reply_text(
            (
                f"⚠️ Você enviou <b>{len(links)}</b> links.\n\n"
                f"O limite é <b>{MAX_LINKS_POR_ENVIO}</b> "
                "links por envio.\n"
                "\n"
                "Envie em grupos menores."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    try:

        (
            adicionados,
            duplicados,
            erros,
            ids_inseridos,
        ) = await asyncio.to_thread(
            inserir_links,
            links,
        )

        mensagem = (
            "🦊 <b>LINKS RECEBIDOS</b>\n"
            "\n"
            f"📥 Recebidos: <b>{len(links)}</b>\n"
            f"⏳ Adicionados à fila: <b>{adicionados}</b>\n"
            f"♻️ Já existentes: <b>{duplicados}</b>\n"
            f"❌ Erros: <b>{len(erros)}</b>\n"
            "\n"
            "Os links adicionados estão salvos no "
            "<b>Supabase</b> e aguardando publicação."
        )

        if bot_ativo:

            mensagem += (
                "\n\n"
                "🟢 A publicação está <b>ATIVA</b>."
            )

        else:

            mensagem += (
                "\n\n"
                "🔴 A publicação está <b>PARADA</b>."
            )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

        if ids_inseridos and len(ids_inseridos) >= 2:

            try:
                await asyncio.to_thread(
                    criar_lote_instagram,
                    supabase,
                    ids_inseridos,
                    str(update.effective_chat.id) if update.effective_chat else None,
                    update.message.message_id,
                )
            except Exception:
                logger.exception("Erro ao criar lote Instagram.")

        if erros:

            await enviar_notificacao_admin(
                context.bot,
                (
                    "⚠️ <b>ERRO AO ADICIONAR LINKS</b>\n"
                    "\n"
                    f"Recebidos: {len(links)}\n"
                    f"Adicionados: {adicionados}\n"
                    f"Duplicados: {duplicados}\n"
                    f"Com erro: {len(erros)}"
                ),
            )

    except Exception as erro:

        logger.exception(
            "Erro ao adicionar links."
        )

        await update.message.reply_text(
            (
                "❌ <b>ERRO AO SALVAR LINKS</b>\n"
                "\n"
                f"{str(erro)[:2000]}"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# WORKER DA FILA
# ============================================================

async def worker_fila(
    bot: Bot,
):

    global bot_ativo

    logger.info(
        "Worker da fila iniciado."
    )

    while True:

        try:

            # ------------------------------------------------
            # STOP
            # ------------------------------------------------

            if not bot_ativo:

                await asyncio.sleep(2)

                continue

            # ------------------------------------------------
            # Recuperar produtos presos
            # ------------------------------------------------

            await asyncio.to_thread(
                recuperar_processamentos_presos,
            )

            # ------------------------------------------------
            # Buscar próximo produto
            # ------------------------------------------------

            produto = await asyncio.to_thread(
                buscar_proximo_produto,
            )

            if not produto:

                logger.info(
                    "Nenhum produto pendente. "
                    "Aguardando novos links..."
                )

                await asyncio.sleep(10)

                continue

            # ------------------------------------------------
            # Processar
            # ------------------------------------------------

            sucesso = await processar_produto(
                bot=bot,
                produto_fila=produto,
            )

            # ------------------------------------------------
            # STOP pode ter sido acionado
            # ------------------------------------------------

            if not bot_ativo:

                continue

            # ------------------------------------------------
            # Se publicou, aguarda intervalo
            # ------------------------------------------------

            if sucesso:

                logger.info(
                    "Aguardando %d minutos "
                    "para o próximo produto...",
                    INTERVALO_MINUTOS,
                )

                for _ in range(
                    INTERVALO_MINUTOS * 60
                ):

                    if not bot_ativo:
                        break

                    await asyncio.sleep(1)

            else:

                logger.warning(
                    "Produto apresentou erro. "
                    "Aguardando 30 segundos antes "
                    "de verificar a fila novamente."
                )

                for _ in range(30):

                    if not bot_ativo:
                        break

                    await asyncio.sleep(1)

        except asyncio.CancelledError:

            logger.info(
                "Worker da fila cancelado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "Erro no worker da fila: %s",
                erro,
            )

            try:

                await enviar_notificacao_admin(
                    bot,
                    (
                        "🚨 <b>ERRO NO WORKER</b>\n"
                        "\n"
                        f"{str(erro)[:1500]}\n"
                        "\n"
                        "O worker tentará continuar automaticamente."
                    ),
                )

            except Exception:

                pass

            await asyncio.sleep(30)


# ============================================================
# INICIAR WORKER
# ============================================================

async def iniciar_worker(
    application: Application,
):

    global worker_task

    if worker_task is not None:

        if not worker_task.done():

            logger.info(
                "Worker já está em execução."
            )

            return

    worker_task = asyncio.create_task(
        worker_fila(
            application.bot
        )
    )

    logger.info(
        "Worker criado."
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):

    try:

        me = await application.bot.get_me()

        logger.info(
            "Telegram conectado: @%s",
            me.username,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao conectar Telegram: %s",
            erro,
        )

        raise

    await iniciar_worker(
        application
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application: Application,
):

    global worker_task

    if worker_task is not None:

        if not worker_task.done():

            logger.info(
                "Cancelando worker..."
            )

            worker_task.cancel()

            try:

                await worker_task

            except asyncio.CancelledError:

                pass

    worker_task = None

    logger.info(
        "Worker da fila finalizado."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    # --------------------------------------------------------
    # SERVIDOR HTTP DO RENDER
    # --------------------------------------------------------

    servidor_thread = threading.Thread(
        target=iniciar_servidor_http,
        daemon=True,
    )

    servidor_thread.start()

    # --------------------------------------------------------
    # CONFIGURAÇÃO
    # --------------------------------------------------------

    validar_configuracao()

    iniciar_supabase()
    carregar_configuracoes_dinamicas()

    # --------------------------------------------------------
    # RECUPERAR PROCESSAMENTOS PRESOS
    # --------------------------------------------------------

    recuperar_processamentos_presos()

    # --------------------------------------------------------
    # APLICAÇÃO TELEGRAM
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --------------------------------------------------------
    # COMANDOS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            comando_start,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            comando_status,
        )
    )

    application.add_handler(
        CommandHandler(
            "fila",
            comando_fila,
        )
    )

    application.add_handler(
        CommandHandler(
            "erros",
            comando_erros,
        )
    )

    application.add_handler(
        CommandHandler(
            "retry",
            comando_retry,
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            comando_stop,
        )
    )

    application.add_handler(
        CommandHandler(
            "iniciar",
            comando_iniciar,
        )
    )

    application.add_handler(
        CommandHandler(
            "reenviar_ultimo",
            comando_reenviar_ultimo,
        )
    )

    # --------------------------------------------------------
    # BOTÕES
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_controle,
        )
    )

    # --------------------------------------------------------
    # RECEBER LINKS
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    # --------------------------------------------------------
    # START POLLING
    # --------------------------------------------------------

    logger.info(
        "Bot Telegram iniciando polling..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        logger.info(
            "🦊 Raposa Caçadora encerrada."
        )

    except Exception as erro:

        logger.exception(
            "Erro fatal: %s",
            erro,
        )

        raise
