import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from supabase import Client, create_client

from manus import (
    ManusAPIError,
    criar_tarefa_carrossel,
    enviar_mensagem_tarefa,
    listar_mensagens_tarefa,
)

logger = logging.getLogger("raposa-cacadora.instagram")
INSTAGRAM_BATCH_SIZE = max(1, int(os.getenv("INSTAGRAM_BATCH_SIZE", "5")))
INSTAGRAM_AUTO_BATCH = os.getenv("INSTAGRAM_AUTO_BATCH", "true").strip().lower() in {"1", "true", "yes", "on"}
INSTAGRAM_PENDING_WORKER = os.getenv("INSTAGRAM_PENDING_WORKER", "true").strip().lower() in {"1", "true", "yes", "on"}
INSTAGRAM_PENDING_INTERVAL = max(5, int(os.getenv("INSTAGRAM_PENDING_INTERVAL", "10")))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
BOT_ID = os.getenv("BOT_ID", os.getenv("FILA_ORIGEM", "raposa-cacadora")).strip()

def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()

def _legenda_valida(caption: str, quantidade: int) -> bool:
    texto = str(caption or "").strip()
    if not texto or re.search(r"https?://|www\.", texto, re.I):
        return False
    proibidos = (
        r"links? dos achadinhos",
        r"encontre os links",
        r"links? na ordem",
        r"acesse os links",
        r"link de cada produto",
    )
    if any(re.search(p, texto, re.I) for p in proibidos):
        return False
    if "@raposacacadora" not in texto or "EU QUERO" not in texto.upper():
        return False
    if not re.search(r"1️⃣|1\.\s", texto):
        return False
    if quantidade >= 5 and not re.search(r"5️⃣|5\.\s", texto):
        return False
    if not re.search(r"#(?:achadosshopee|shopee|raposacacadora)\b", texto, re.I):
        return False
    return True

def _normalizar_legenda(caption: str, produtos: list[dict[str, Any]]) -> str:
    texto = str(caption or "").strip()
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"\n?Links? dos achadinhos:?\s*", "", texto, flags=re.I)
    texto = re.sub(r"\n?Encontre os links[^\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\n?Acesse os links[^\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\n?Links? na ordem[^\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    nomes = [str(p.get("productName") or p.get("product_name") or p.get("name") or "Achadinho").strip() for p in produtos]
    if not re.search(r"1️⃣|1\.\s", texto):
        texto += "\n\n" + "\n".join(f"{i}️⃣ {nome}" for i, nome in enumerate(nomes, 1))
    if "EU QUERO" not in texto.upper() or "@raposacacadora" not in texto:
        texto += '\n\nTudo que você tá vendo aqui tá com LINK NA BIO e nos STORIES! 👇\n\n👉 Curte se você amou.\n👉 Segue @raposacacadora pra não perder nenhum achado.\n👉 Comenta "EU QUERO" que te mando todos os links no direct. 💌'
    if not re.search(r"#(?:achadosshopee|shopee|raposacacadora)\b", texto, re.I):
        texto += "\n\n#achadosshopee #shopee #raposacacadora"
    return texto

def criar_lote_instagram(supabase: Client, produto_ids: list[int], source_chat_id: str | None = None, source_message_id: int | None = None, bot_id: str | None = None) -> int | None:
    if not INSTAGRAM_AUTO_BATCH or not produto_ids:
        return None
    ids = list(dict.fromkeys(int(x) for x in produto_ids))
    bot_id = (bot_id or BOT_ID).strip()
    primeiro_post_id = None
    for inicio in range(0, len(ids), INSTAGRAM_BATCH_SIZE):
        grupo = ids[inicio:inicio + INSTAGRAM_BATCH_SIZE]
        if len(grupo) < INSTAGRAM_BATCH_SIZE:
            logger.info("Grupo Instagram aguardando completar %d produtos: %d/%d.", INSTAGRAM_BATCH_SIZE, len(grupo), INSTAGRAM_BATCH_SIZE)
            continue
        post = supabase.table("instagram_posts").insert({"status": "pending", "bot_id": bot_id, "source_chat_id": source_chat_id, "source_message_id": source_message_id}).execute()
        if not post.data:
            raise RuntimeError("Não foi possível criar o lote Instagram.")
        post_id = int(post.data[0]["id"])
        if primeiro_post_id is None:
            primeiro_post_id = post_id
        rows = [{"instagram_post_id": post_id, "produto_fila_id": produto_id, "position": position} for position, produto_id in enumerate(grupo, start=1)]
        supabase.table("instagram_post_products").insert(rows).execute()
        logger.info("Lote Instagram #%s criado com %d produto(s).", post_id, len(grupo))
    return primeiro_post_id

def registrar_produto_processado(supabase: Client, produto_id: int, produto: dict[str, Any]) -> list[int]:
    relacionamentos = supabase.table("instagram_post_products").select("id,instagram_post_id").eq("produto_fila_id", produto_id).execute()
    post_ids = []
    snapshot = dict(produto)
    snapshot.pop("raw_response", None)
    for rel in relacionamentos.data or []:
        supabase.table("instagram_post_products").update({"product_snapshot": snapshot}).eq("id", rel["id"]).execute()
        post_ids.append(int(rel["instagram_post_id"]))
    return post_ids

def _buscar_produtos_do_lote(supabase: Client, post_id: int) -> list[dict[str, Any]]:
    rows = supabase.table("instagram_post_products").select("position,product_snapshot,produto_fila_id").eq("instagram_post_id", post_id).order("position").execute()
    produtos = []
    for row in rows.data or []:
        if isinstance(row.get("product_snapshot"), dict):
            produto = dict(row["product_snapshot"])
            produto["id"] = row.get("produto_fila_id")
            produtos.append(produto)
    return produtos

def _extrair_attachments(detail: dict[str, Any], messages: dict[str, Any] | None = None) -> list[dict[str, str]]:
    encontrados, vistos = [], set()
    def adicionar(item: Any):
        if not isinstance(item, dict):
            return
        url = str(item.get("url") or item.get("download_url") or item.get("asset_url") or item.get("downloadUrl") or "").strip()
        if not url.startswith(("https://", "http://")):
            return
        nome = str(item.get("file_name") or item.get("filename") or item.get("name") or "imagem").strip()
        chave = (nome, url)
        if chave not in vistos:
            vistos.add(chave)
            encontrados.append({"file_name": nome, "url": url})
    def percorrer(value: Any):
        if isinstance(value, dict):
            adicionar(value)
            for v in value.values():
                percorrer(v)
        elif isinstance(value, list):
            for v in value:
                percorrer(v)
    percorrer(detail)
    if messages:
        percorrer(messages)
    return encontrados

def _salvar_file_ids(supabase: Client | None, post_id: int, file_ids: list[str]) -> None:
    if supabase is None or not file_ids:
        return
    try:
        row = supabase.table("instagram_posts").select("assets").eq("id", post_id).limit(1).execute()
        assets = row.data[0].get("assets") if row.data else []
        if not isinstance(assets, list):
            return
        novos = []
        for idx, asset in enumerate(assets):
            item = dict(asset) if isinstance(asset, dict) else {"asset_url": str(asset)}
            if idx < len(file_ids):
                item["telegram_file_id"] = file_ids[idx]
            novos.append(item)
        supabase.table("instagram_posts").update({"assets": novos, "updated_at": _agora()}).eq("id", post_id).execute()
    except Exception:
        logger.exception("Não foi possível salvar file_id do Telegram para o lote #%s.", post_id)

def _extrair_file_ids_telegram(resposta: requests.Response) -> list[str]:
    file_ids = []
    try:
        for msg in resposta.json().get("result", []):
            photos = msg.get("photo") or []
            if photos:
                file_ids.append(str(photos[-1].get("file_id") or ""))
    except Exception:
        logger.exception("Não foi possível extrair file_ids do Telegram.")
    return file_ids


def _extrair_urls_de_conteudo_manus(conteudo: str, base_url: str | None = None) -> list[str]:
    """Extrai URLs de imagens quando a URL de download do Manus retorna Markdown/HTML."""
    urls = []
    vistos = set()
    padroes = [
        r"!\\[[^\\]]*\\]\\((https?://[^)\\s]+)",
        r"<img[^>]+src=[\\\"'](https?://[^\\\"']+)",
        r"https?://[^\\s)\\\"'<>]+",
    ]
    for padrao in padroes:
        for encontrado in re.findall(padrao, conteudo or "", flags=re.I):
            url = str(encontrado).strip().rstrip(".,;")
            if url.startswith(("http://", "https://")) and url not in vistos:
                vistos.add(url)
                urls.append(url)
    return urls


def _baixar_imagem_para_telegram(url: str, _tentativa: int = 0) -> tuple[bytes, str]:
    resposta = requests.get(
        url,
        timeout=45,
        allow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 RaposaCacadora/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    resposta.raise_for_status()
    conteudo = resposta.content
    if not conteudo:
        raise RuntimeError("arquivo vazio")
    tipo = (resposta.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if tipo.startswith("image/"):
        return conteudo, tipo

    # Alguns downloads do Manus retornam uma página Markdown/HTML que contém
    # a URL assinada do arquivo real. Resolve essa camada automaticamente.
    if _tentativa == 0 and tipo in {"text/markdown", "text/html", "text/plain", ""}:
        candidatos = _extrair_urls_de_conteudo_manus(
            conteudo.decode("utf-8", errors="ignore"),
            resposta.url,
        )
        for candidato in candidatos:
            try:
                return _baixar_imagem_para_telegram(candidato, _tentativa=1)
            except Exception:
                continue

    raise RuntimeError(f"conteúdo não é imagem: {tipo or 'content-type ausente'}")


def _enviar_grupo_telegram_por_arquivo(base: str, chat_id: int, grupo: list[dict[str, str]], post_id: int, caption: str, inicio: int) -> requests.Response:
    media = []
    files = {}
    try:
        for pos, item in enumerate(grupo):
            nome = item.get("file_name") or f"carrossel-{post_id}-{inicio + pos + 1}.jpg"
            chave = f"file{pos}"
            conteudo, tipo = _baixar_imagem_para_telegram(item["url"])
            media_item = {"type": "photo", "media": f"attach://{chave}"}
            if inicio == 0 and pos == 0:
                media_item["caption"] = f"🦊 <b>CARROSSEL #{post_id}</b>\n\n{caption[:900]}"
                media_item["parse_mode"] = "HTML"
            media.append(media_item)
            files[chave] = (nome, conteudo, tipo)
        return requests.post(
            f"{base}/sendMediaGroup",
            data={
                "chat_id": str(chat_id),
                "media": json.dumps(media, ensure_ascii=False),
            },
            files=files,
            timeout=90,
        )
    finally:
        files.clear()


def _enviar_preview_telegram(post_id: int, detail: dict[str, Any], attachments: list[dict[str, str]], caption: str, task_url: str | None, supabase: Client | None = None) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_ADMIN_ID or not attachments:
        logger.warning("Preview Telegram não enviado: token/chat/attachments ausente.")
        return False
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    chat_id = int(TELEGRAM_ADMIN_ID)
    enviados = 0
    file_ids = []
    for inicio in range(0, len(attachments), 10):
        grupo = attachments[inicio:inicio + 10]
        media = []
        for pos, item in enumerate(grupo):
            parte = {"type": "photo", "media": item["url"]}
            if inicio == 0 and pos == 0:
                parte["caption"] = f"🦊 <b>CARROSSEL #{post_id}</b>\n\n{caption[:900]}"
                parte["parse_mode"] = "HTML"
            media.append(parte)
        try:
            resposta = requests.post(
                f"{base}/sendMediaGroup",
                json={"chat_id": chat_id, "media": media},
                timeout=60,
            )
        except Exception as exc:
            logger.warning("Falha de rede ao enviar preview do lote #%s por URL: %s", post_id, exc)
            resposta = None

        if resposta is not None and resposta.ok:
            file_ids.extend(_extrair_file_ids_telegram(resposta))
            enviados += len(grupo)
            continue

        erro_url = (resposta.text[:1000] if resposta is not None else "sem resposta")
        logger.warning(
            "Telegram recusou preview do lote #%s por URL; tentando baixar e enviar os arquivos diretamente: %s",
            post_id,
            erro_url,
        )
        try:
            resposta_arquivo = _enviar_grupo_telegram_por_arquivo(
                base, chat_id, grupo, post_id, caption, inicio
            )
        except Exception as exc:
            logger.error(
                "Não foi possível baixar/enviar as imagens do lote #%s diretamente: %s",
                post_id,
                exc,
            )
            return False

        if not resposta_arquivo.ok:
            logger.error(
                "Telegram recusou os arquivos do preview do lote #%s: %s",
                post_id,
                resposta_arquivo.text[:1000],
            )
            return False

        file_ids.extend(_extrair_file_ids_telegram(resposta_arquivo))
        enviados += len(grupo)
        logger.info(
            "Preview do lote #%s enviado por upload direto após falha da URL.",
            post_id,
        )

    if supabase and file_ids:
        _salvar_file_ids(supabase, post_id, file_ids)
    texto = f"🦊 <b>CARROSSEL #{post_id} RECEBIDO DA MANUS</b>\n\n📸 <b>{enviados}</b> imagem(ns) enviadas acima.\n👀 Revise a capa e os produtos antes da publicação.\nEscolha uma opção abaixo:"
    if task_url:
        texto += f"\n\n🔗 <a href=\"{task_url}\">Abrir tarefa no Manus</a>"
    resposta = requests.post(f"{base}/sendMessage", json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": {"inline_keyboard": [[{"text": "✅ APROVAR", "callback_data": f"carousel_approve:{post_id}"}, {"text": "❌ REPROVAR", "callback_data": f"carousel_reject:{post_id}"}]]}}, timeout=30)
    if not resposta.ok:
        logger.error("Telegram recusou os botões do preview do lote #%s: %s", post_id, resposta.text[:1000])
    return resposta.ok

def processar_lote_se_pronto(supabase: Client, post_id: int) -> bool:
    post_response = supabase.table("instagram_posts").select("*").eq("id", post_id).eq("bot_id", BOT_ID).limit(1).execute()
    if not post_response.data or post_response.data[0].get("status") != "pending":
        return False
    produtos = _buscar_produtos_do_lote(supabase, post_id)
    if len(produtos) < INSTAGRAM_BATCH_SIZE:
        return False
    produtos = produtos[:INSTAGRAM_BATCH_SIZE]
    reservado = supabase.table("instagram_posts").update({"status": "manus_processing", "updated_at": _agora()}).eq("id", post_id).eq("status", "pending").execute()
    if not reservado.data:
        return False
    try:
        resultado = criar_tarefa_carrossel(produtos, post_id)
        task = resultado.get("task_detail") or resultado.get("task") or {}
        task_id = task.get("task_id") or resultado.get("task_id")
        task_url = task.get("task_url") or resultado.get("task_url")
        if not task_id:
            raise ManusAPIError(f"Manus não retornou task_id: {resultado}")
        supabase.table("instagram_posts").update({"manus_task_id": task_id, "manus_task_url": task_url, "prompt": "Carrossel Instagram criado automaticamente pela pipeline; preview enviado ao Telegram quando concluído.", "updated_at": _agora()}).eq("id", post_id).execute()
        logger.info("Lote Instagram #%s enviado para Manus: %s", post_id, task_id)
        return True
    except Exception as exc:
        supabase.table("instagram_posts").update({"status": "error", "error": str(exc)[:4000], "updated_at": _agora()}).eq("id", post_id).execute()
        logger.exception("Falha no lote Instagram #%s.", post_id)
        return False

def processar_lotes_pendentes(supabase: Client, limite: int = 10, bot_id: str | None = None) -> int:
    """Busca lotes pending já completos e dispara o Manus mesmo que os produtos tenham sido processados antes."""
    bot_id = (bot_id or BOT_ID).strip()
    resposta = supabase.table("instagram_posts").select("id").eq("status", "pending").eq("bot_id", bot_id).order("id").limit(limite).execute()
    enviados = 0
    for row in resposta.data or []:
        try:
            if processar_lote_se_pronto(supabase, int(row["id"])):
                enviados += 1
        except Exception:
            logger.exception("Erro ao processar lote Instagram pendente #%s.", row.get("id"))
    return enviados

def _worker_lotes_pendentes() -> None:
    if not INSTAGRAM_PENDING_WORKER:
        return
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        logger.warning("Worker Instagram não iniciado: SUPABASE_URL/SUPABASE_KEY ausentes.")
        return
    try:
        cliente = create_client(url, key)
    except Exception:
        logger.exception("Não foi possível criar cliente Supabase do worker Instagram.")
        return
    logger.info("Worker de lotes Instagram pendentes iniciado; intervalo=%ss.", INSTAGRAM_PENDING_INTERVAL)
    while True:
        try:
            quantidade = processar_lotes_pendentes(cliente, limite=10, bot_id=BOT_ID)
            if quantidade:
                logger.info("Worker Instagram enviou %d lote(s) pendente(s) para o Manus.", quantidade)
        except Exception:
            logger.exception("Erro no worker de lotes Instagram pendentes.")
        time.sleep(INSTAGRAM_PENDING_INTERVAL)

def iniciar_worker_lotes_pendentes() -> None:
    if not INSTAGRAM_PENDING_WORKER:
        return
    thread = threading.Thread(target=_worker_lotes_pendentes, name="instagram-pending-worker", daemon=True)
    thread.start()


def processar_webhook_manus(supabase: Client, payload: dict[str, Any]) -> tuple[bool, str]:
    event_type = payload.get("event_type")
    detail = payload.get("task_detail") or {}
    task_id = detail.get("task_id")
    if not task_id:
        return False, "task_id ausente"
    post_response = supabase.table("instagram_posts").select("id,status,bot_id").eq("manus_task_id", task_id).eq("bot_id", BOT_ID).limit(1).execute()
    if not post_response.data:
        return True, "tarefa ignorada: task_id não pertence a esta pipeline"
    post_id = int(post_response.data[0]["id"])
    if event_type == "task_created":
        return True, "task_created registrado"
    if event_type == "task_stopped" and detail.get("stop_reason") == "finish":
        try:
            structured = detail.get("structured_output") or {}
            if not structured.get("success", False):
                raise ManusAPIError(structured.get("error") or "Manus não retornou structured output.")
            value = structured.get("value") or {}
            produtos = _buscar_produtos_do_lote(supabase, post_id)
            caption = str(value.get("caption") or "")
            if not _legenda_valida(caption, len(produtos)):
                logger.warning("Legenda inválida no lote #%s; solicitando correção ao Manus sem publicar.", post_id)
                enviar_mensagem_tarefa(task_id, "A legenda retornada está fora do padrão obrigatório da Raposa Caçadora. REFAÇA SOMENTE o campo caption. PROIBIDO: qualquer URL http/https, www., 'Links dos achadinhos', 'Encontre os links', 'links na ordem dos slides', 'Acesse os links' ou 'link de cada produto'. OBRIGATÓRIO: lista numerada dos produtos, CTA com LINK NA BIO e nos STORIES, @raposacacadora, comentário 'EU QUERO', pergunta final e hashtags. Não publique nada ainda; aguarde a validação da legenda corrigida.")
                return True, "legenda inválida; correção solicitada ao Manus"
            attachments = _extrair_attachments(detail)
            # O webhook pode trazer URLs temporárias ou não trazer todos os
            # attachments. Sempre consulta o histórico da tarefa para obter
            # as URLs de download atuais e complementa o conjunto encontrado.
            try:
                mensagens_manus = listar_mensagens_tarefa(task_id)
                attachments_historico = _extrair_attachments({}, mensagens_manus)
                existentes = {item["url"] for item in attachments}
                attachments.extend(item for item in attachments_historico if item["url"] not in existentes)
            except Exception:
                logger.exception("Não foi possível recuperar attachments da tarefa Manus %s", task_id)
            caption = _normalizar_legenda(caption, produtos)
            supabase.table("instagram_posts").update({"status": "ready", "category": value.get("category"), "caption": caption, "assets": value.get("slides"), "manus_result": structured, "updated_at": _agora()}).eq("id", post_id).execute()
            enviados = _enviar_preview_telegram(post_id, detail, attachments, caption, str(detail.get("task_url") or "") or None, supabase)
            return True, "lote pronto e preview enviado ao Telegram" if enviados else "lote pronto; preview Telegram não enviado"
        except Exception as exc:
            supabase.table("instagram_posts").update({"status": "error", "error": str(exc)[:4000], "updated_at": _agora()}).eq("id", post_id).execute()
            return False, "falha ao recuperar resultado Manus"
    return True, "evento ignorado"


# Inicia o worker após os imports, sem depender do processamento de um produto novo.
iniciar_worker_lotes_pendentes()
