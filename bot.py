
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
from cryptography.hazmat.primitives.asymmetric import padding, ed25519

from instagram_pipeline import processar_webhook_manus

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

TELEGRAM_BOT_ID = ""

TELEGRAM_ADMIN_ID = os.getenv(
    "TELEGRAM_ADMIN_ID",
    "",
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
        "20",
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

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://raposa-cacadora-git-main-raposacacadora.vercel.app/",
).strip()

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
                diagnostico = diagnosticar_telegram_webapp(init_data)
                logger.info(
                    "Web App auth recebido: init_len=%d hash=%s user=%s auth_date=%s token_configurado=%s signature_valida=%s",
                    diagnostico["init_len"],
                    diagnostico["hash"],
                    diagnostico["user"],
                    diagnostico["auth_date"],
                    diagnostico["token_configurado"],
                    diagnostico.get("signature_valida"),
                )
                if not diagnostico["valido"]:
                    logger.warning(
                        "Web App auth rejeitado: motivo=%s chaves=%s auth_age=%s",
                        diagnostico["motivo"],
                        ",".join(diagnostico["chaves"]),
                        diagnostico["auth_age"],
                    )
                    logger.warning(
                        "Web App auth diagnóstico: hash=%s calculado=%s reverso=%s data_len=%s data_sha=%s token_fp=%s",
                        diagnostico.get("hash_prefix"),
                        diagnostico.get("calculado_prefix"),
                        diagnostico.get("reverso_prefix"),
                        diagnostico.get("data_check_len"),
                        diagnostico.get("data_check_sha256"),
                        diagnostico.get("token_fingerprint"),
                    )
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

def validar_assinatura_telegram(init_data: str) -> bool:
    if not init_data or not TELEGRAM_BOT_ID:
        return False
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        signature = params.pop("signature", "")
        params.pop("hash", None)
        if not signature:
            return False
        data_check_string = f"{TELEGRAM_BOT_ID}:WebAppData\\n" + "\\n".join(
            f"{k}={params[k]}" for k in sorted(params)
        )
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex("e7bf03a2fa4600703d88dda5bb59f32ed8b02a56c187fe7d34caed242")
        )
        # Telegram usa base64url sem padding para a assinatura.
        padded = signature + "=" * (-len(signature) % 4)
        public_key.verify(
            base64.urlsafe_b64decode(padded),
            data_check_string.encode("utf-8"),
        )
        return True
    except Exception:
        return False


def diagnosticar_telegram_webapp(init_data: str) -> dict:
    resultado = {
        "valido": False,
        "motivo": "init_data_vazio",
        "init_len": len(init_data or ""),
        "hash": False,
        "user": False,
        "auth_date": False,
        "auth_age": None,
        "token_configurado": bool(TELEGRAM_TOKEN),
        "chaves": [],
    }

    if not init_data:
        return resultado

    if not TELEGRAM_TOKEN:
        resultado["motivo"] = "token_nao_configurado"
        return resultado

    try:
        params = dict(
            urllib.parse.parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )
        resultado["chaves"] = sorted(params.keys())
        recebido = params.pop("hash", "")
        params.pop("signature", None)
        resultado["hash"] = bool(recebido)
        resultado["user"] = bool(params.get("user"))
        resultado["signature_valida"] = validar_assinatura_telegram(init_data)

        auth_date = params.get("auth_date")
        if auth_date:
            try:
                idade = int(time.time()) - int(auth_date)
                resultado["auth_age"] = idade
                resultado["auth_date"] = True
                if idade > 86400:
                    resultado["motivo"] = "auth_date_expirado"
                    return resultado
            except (TypeError, ValueError):
                resultado["motivo"] = "auth_date_invalido"
                return resultado

        if not recebido:
            resultado["motivo"] = "hash_ausente"
            return resultado

        data_check_string = "\n".join(
            f"{k}={params[k]}"
            for k in sorted(params)
        )
        secret_key = hmac.new(
            TELEGRAM_TOKEN.encode("utf-8"),
            b"WebAppData",
            hashlib.sha256,
        ).digest()
        calculado = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Diagnóstico seguro: não registra token nem initData completo.
        resultado["hash_prefix"] = recebido[:12]
        resultado["calculado_prefix"] = calculado[:12]
        resultado["data_check_len"] = len(data_check_string)
        resultado["data_check_sha256"] = hashlib.sha256(
            data_check_string.encode("utf-8")
        ).hexdigest()[:12]
        resultado["token_fingerprint"] = hashlib.sha256(
            TELEGRAM_TOKEN.encode("utf-8")
        ).hexdigest()[:12]

        # Compara variantes apenas em memória para identificar divergência
        # de algoritmo/normalização sem expor dados sensíveis nos logs.
        calculado_com_signature = hmac.new(
            secret_key,
            "\n".join(
                f"{k}={params[k]}"
                for k in sorted(params | {"signature": ""})
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest() if False else ""
        calculado_reverso = hmac.new(
            b"WebAppData",
            TELEGRAM_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        calculado_reverso = hmac.new(
            calculado_reverso,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        resultado["reverso_prefix"] = calculado_reverso[:12]

        if not hmac.compare_digest(calculado, recebido):
            if resultado.get("signature_valida"):
                resultado["motivo"] = "hash_invalido_token_mismatch_signature_ok"
                resultado["valido"] = True
                return resultado
            resultado["motivo"] = "hash_invalido"
            return resultado

        resultado["valido"] = True
        resultado["motivo"] = "ok"
        return resultado

    except Exception as erro:
        logger.exception("Erro no diagnóstico do Telegram Web App: %s", erro)
        resultado["motivo"] = "parse_erro"
        return resultado


def validar_telegram_webapp(init_data: str) -> bool:
    if not init_data or not TELEGRAM_TOKEN:
        return False
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        recebido = params.pop("hash", "")
        params.pop("signature", None)
        if not recebido:
            return False
        data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret_key = hmac.new(
            TELEGRAM_TOKEN.encode("utf-8"),
            b"WebAppData",
            hashlib.sha256,
        ).digest()
        calculado = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
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
                    "🦊 ABRIR WEB APP",
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

    if query.data == "bot_stop":

        bot_ativo = False

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

        global TELEGRAM_BOT_ID
        TELEGRAM_BOT_ID = str(me.id)
        logger.info(
            "Telegram conectado: @%s (id=%s)",
            me.username,
            me.id,
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
