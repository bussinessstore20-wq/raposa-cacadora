import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from supabase import Client

from manus import ManusAPIError, criar_tarefa_carrossel, listar_mensagens_tarefa

logger = logging.getLogger("raposa-cacadora.instagram")
INSTAGRAM_BATCH_SIZE = max(1, int(os.getenv("INSTAGRAM_BATCH_SIZE", "5")))
INSTAGRAM_AUTO_BATCH = os.getenv("INSTAGRAM_AUTO_BATCH", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()

def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()

def criar_lote_instagram(supabase: Client, produto_ids: list[int], source_chat_id: str | None = None, source_message_id: int | None = None) -> int | None:
    if not INSTAGRAM_AUTO_BATCH or not produto_ids:
        return None
    ids = list(dict.fromkeys(int(x) for x in produto_ids))
    post = supabase.table("instagram_posts").insert({"status": "pending", "source_chat_id": source_chat_id, "source_message_id": source_message_id}).execute()
    if not post.data:
        raise RuntimeError("Não foi possível criar o lote Instagram.")
    post_id = int(post.data[0]["id"])
    rows = [{"instagram_post_id": post_id, "produto_fila_id": produto_id, "position": position} for position, produto_id in enumerate(ids, start=1)]
    supabase.table("instagram_post_products").insert(rows).execute()
    logger.info("Lote Instagram #%s criado com %d produto(s).", post_id, len(ids))
    return post_id

def registrar_produto_processado(supabase: Client, produto_id: int, produto: dict[str, Any]) -> list[int]:
    relacionamentos = supabase.table("instagram_post_products").select("id,instagram_post_id").eq("produto_fila_id", produto_id).execute()
    post_ids: list[int] = []
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
        snapshot = row.get("product_snapshot")
        if isinstance(snapshot, dict):
            produto = dict(snapshot)
            produto["id"] = row.get("produto_fila_id")
            produtos.append(produto)
    return produtos

def _montar_resultado_manus(messages: dict[str, Any]) -> dict[str, Any]:
    return {"raw": messages, "captured_at": _agora()}

def _extrair_attachments(detail: dict[str, Any], messages: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Extrai attachments HTTP da resposta Manus, inclusive em estruturas aninhadas."""
    encontrados: list[dict[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    def adicionar(item: Any):
        if not isinstance(item, dict):
            return
        url = str(item.get("url") or item.get("download_url") or item.get("asset_url") or "").strip()
        if not url.startswith(("https://", "http://")):
            return
        nome = str(item.get("file_name") or item.get("filename") or item.get("name") or "imagem").strip()
        chave = (nome, url)
        if chave not in vistos:
            vistos.add(chave)
            encontrados.append({"file_name": nome, "url": url})
    def percorrer(value: Any):
        if isinstance(value, dict):
            if any(k in value for k in ("url", "download_url", "asset_url")):
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

def _enviar_preview_telegram(post_id: int, detail: dict[str, Any], attachments: list[dict[str, str]], caption: str, task_url: str | None, supabase: Client | None = None) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_ADMIN_ID or not attachments:
        logger.warning("Preview Telegram não enviado: token/chat/attachments ausente.")
        return False
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    enviados = 0
    file_ids: list[str] = []
    for inicio in range(0, len(attachments), 10):
        grupo = attachments[inicio:inicio + 10]
        media = []
        for pos, item in enumerate(grupo):
            parte = {"type": "photo", "media": item["url"]}
            if inicio == 0 and pos == 0:
                parte["caption"] = f"🦊 <b>CARROSSEL #{post_id}</b>\n\n{caption[:900]}"
                parte["parse_mode"] = "HTML"
            media.append(parte)
        resposta = requests.post(f"{base}/sendMediaGroup", json={"chat_id": int(TELEGRAM_ADMIN_ID), "media": media}, timeout=60)
        if not resposta.ok:
            logger.error("Telegram recusou preview do lote #%s: %s", post_id, resposta.text[:1000])
            return False
        try:
            for msg in resposta.json().get("result", []):
                photos = msg.get("photo") or []
                if photos:
                    file_ids.append(str(photos[-1].get("file_id") or ""))
        except Exception:
            logger.exception("Não foi possível extrair file_ids do Telegram para o lote #%s.", post_id)
        enviados += len(grupo)
    if supabase and file_ids:
        _salvar_file_ids(supabase, post_id, file_ids)
    texto = f"🦊 <b>CARROSSEL #{post_id} RECEBIDO DA MANUS</b>\n\n📸 <b>{enviados}</b> imagem(ns) enviadas acima.\n👀 Revise a capa e os produtos antes da publicação.\nEscolha uma opção abaixo:"
    if task_url:
        texto += f"\n\n🔗 <a href=\"{task_url}\">Abrir tarefa no Manus</a>"
    resposta = requests.post(f"{base}/sendMessage", json={"chat_id": int(TELEGRAM_ADMIN_ID), "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": {"inline_keyboard": [[{"text": "✅ APROVAR", "callback_data": f"carousel_approve:{post_id}"}, {"text": "❌ REPROVAR", "callback_data": f"carousel_reject:{post_id}"}]]}}, timeout=30)
    if not resposta.ok:
        logger.error("Telegram recusou os botões do preview do lote #%s: %s", post_id, resposta.text[:1000])
    return resposta.ok

def processar_lote_se_pronto(supabase: Client, post_id: int) -> bool:
    post_response = supabase.table("instagram_posts").select("*").eq("id", post_id).limit(1).execute()
    if not post_response.data or post_response.data[0].get("status") != "pending":
        return False
    produtos = _buscar_produtos_do_lote(supabase, post_id)
    total = supabase.table("instagram_post_products").select("id", count="exact").eq("instagram_post_id", post_id).execute().count or 0
    if total < INSTAGRAM_BATCH_SIZE or len(produtos) < total:
        return False
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

def processar_webhook_manus(supabase: Client, payload: dict[str, Any]) -> tuple[bool, str]:
    event_type = payload.get("event_type")
    detail = payload.get("task_detail") or {}
    task_id = detail.get("task_id")
    if not task_id:
        return False, "task_id ausente"
    post_response = supabase.table("instagram_posts").select("id,status").eq("manus_task_id", task_id).limit(1).execute()
    if not post_response.data:
        return True, "tarefa ignorada: task_id não pertence a esta pipeline"
    post_id = int(post_response.data[0]["id"])
    if event_type == "task_created":
        return True, "task_created registrado"
    if event_type == "task_stopped":
        stop_reason = detail.get("stop_reason")
        if stop_reason == "finish":
            try:
                structured = detail.get("structured_output") or {}
                if not structured.get("success", False):
                    raise ManusAPIError(structured.get("error") or "Manus não retornou structured output.")
                value = structured.get("value") or {}
                attachments = _extrair_attachments(detail)
                if not attachments:
                    try:
                        messages = listar_mensagens_tarefa(task_id)
                        attachments = _extrair_attachments({}, messages)
                    except Exception:
                        logger.exception("Não foi possível recuperar attachments da tarefa Manus %s.", task_id)
                supabase.table("instagram_posts").update({"status": "ready", "category": value.get("category"), "caption": value.get("caption"), "assets": value.get("slides"), "manus_result": structured, "updated_at": _agora()}).eq("id", post_id).execute()
                enviados = _enviar_preview_telegram(post_id=post_id, detail=detail, attachments=attachments, caption=str(value.get("caption") or ""), task_url=str(detail.get("task_url") or "") or None, supabase=supabase)
                if enviados:
                    return True, "lote pronto e preview enviado ao Telegram"
                return True, "lote pronto; preview Telegram não enviado"
            except Exception as exc:
                supabase.table("instagram_posts").update({"status": "error", "error": str(exc)[:4000], "updated_at": _agora()}).eq("id", post_id).execute()
                return False, "falha ao recuperar resultado Manus"
        supabase.table("instagram_posts").update({"status": "error", "error": detail.get("message") or "Manus interrompeu a tarefa.", "updated_at": _agora()}).eq("id", post_id).execute()
        return False, "tarefa Manus não concluída"
    return True, "evento ignorado"
