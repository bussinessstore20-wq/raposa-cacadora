import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client

from manus import ManusAPIError, criar_tarefa_carrossel, listar_mensagens_tarefa


logger = logging.getLogger("raposa-cacadora.instagram")

INSTAGRAM_BATCH_SIZE = max(1, int(os.getenv("INSTAGRAM_BATCH_SIZE", "5")))
INSTAGRAM_AUTO_BATCH = os.getenv("INSTAGRAM_AUTO_BATCH", "true").strip().lower() in {
    "1", "true", "yes", "on"
}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def criar_lote_instagram(
    supabase: Client,
    produto_ids: list[int],
    source_chat_id: str | None = None,
    source_message_id: int | None = None,
) -> int | None:
    if not INSTAGRAM_AUTO_BATCH or not produto_ids:
        return None

    ids = list(dict.fromkeys(int(x) for x in produto_ids))
    if len(ids) < INSTAGRAM_BATCH_SIZE:
        logger.info(
            "Lote Instagram ainda não atingiu %d produtos; recebidos=%d.",
            INSTAGRAM_BATCH_SIZE,
            len(ids),
        )

    post = (
        supabase.table("instagram_posts")
        .insert({
            "status": "pending",
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
        })
        .execute()
    )

    if not post.data:
        raise RuntimeError("Não foi possível criar o lote Instagram.")

    post_id = int(post.data[0]["id"])

    rows = [
        {
            "instagram_post_id": post_id,
            "produto_fila_id": produto_id,
            "position": position,
        }
        for position, produto_id in enumerate(ids, start=1)
    ]

    supabase.table("instagram_post_products").insert(rows).execute()

    logger.info(
        "Lote Instagram #%s criado com %d produto(s).",
        post_id,
        len(ids),
    )
    return post_id


def registrar_produto_processado(
    supabase: Client,
    produto_id: int,
    produto: dict[str, Any],
) -> list[int]:
    """Salva o snapshot do produto em todos os lotes aos quais ele pertence."""
    relacionamentos = (
        supabase.table("instagram_post_products")
        .select("id,instagram_post_id")
        .eq("produto_fila_id", produto_id)
        .execute()
    )

    post_ids: list[int] = []

    snapshot = dict(produto)
    snapshot.pop("raw_response", None)

    for rel in relacionamentos.data or []:
        supabase.table("instagram_post_products").update({
            "product_snapshot": snapshot,
        }).eq("id", rel["id"]).execute()
        post_ids.append(int(rel["instagram_post_id"]))

    return post_ids


def _buscar_produtos_do_lote(supabase: Client, post_id: int) -> list[dict[str, Any]]:
    rows = (
        supabase.table("instagram_post_products")
        .select("position,product_snapshot,produto_fila_id")
        .eq("instagram_post_id", post_id)
        .order("position")
        .execute()
    )
    produtos = []
    for row in rows.data or []:
        snapshot = row.get("product_snapshot")
        if isinstance(snapshot, dict):
            produto = dict(snapshot)
            produto["id"] = row.get("produto_fila_id")
            produtos.append(produto)
    return produtos


def _montar_resultado_manus(messages: dict[str, Any]) -> dict[str, Any]:
    """Guarda a resposta da Manus sem presumir um formato específico de asset."""
    return {
        "raw": messages,
        "captured_at": _agora(),
    }


def processar_lote_se_pronto(supabase: Client, post_id: int) -> bool:
    post_response = (
        supabase.table("instagram_posts")
        .select("*")
        .eq("id", post_id)
        .limit(1)
        .execute()
    )
    if not post_response.data:
        return False

    post = post_response.data[0]
    if post.get("status") != "pending":
        return False

    produtos = _buscar_produtos_do_lote(supabase, post_id)
    total = (
        supabase.table("instagram_post_products")
        .select("id", count="exact")
        .eq("instagram_post_id", post_id)
        .execute()
    ).count or 0

    if total < INSTAGRAM_BATCH_SIZE or len(produtos) < total:
        return False

    # Reserva o lote antes de chamar a API externa, evitando chamadas duplicadas.
    reservado = (
        supabase.table("instagram_posts")
        .update({
            "status": "manus_processing",
            "updated_at": _agora(),
        })
        .eq("id", post_id)
        .eq("status", "pending")
        .execute()
    )
    if not reservado.data:
        return False

    try:
        resultado = criar_tarefa_carrossel(produtos, post_id)
        task = resultado.get("task_detail") or resultado.get("task") or {}
        task_id = task.get("task_id") or resultado.get("task_id")
        task_url = task.get("task_url") or resultado.get("task_url")

        if not task_id:
            raise ManusAPIError(f"Manus não retornou task_id: {resultado}")

        supabase.table("instagram_posts").update({
            "manus_task_id": task_id,
            "manus_task_url": task_url,
            "prompt": "Carrossel Instagram criado automaticamente pela pipeline.",
            "updated_at": _agora(),
        }).eq("id", post_id).execute()

        logger.info(
            "Lote Instagram #%s enviado para Manus: %s",
            post_id,
            task_id,
        )
        return True

    except Exception as exc:
        supabase.table("instagram_posts").update({
            "status": "error",
            "error": str(exc)[:4000],
            "updated_at": _agora(),
        }).eq("id", post_id).execute()
        logger.exception("Falha no lote Instagram #%s.", post_id)
        return False


def processar_webhook_manus(
    supabase: Client,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    event_type = payload.get("event_type")
    detail = payload.get("task_detail") or {}
    task_id = detail.get("task_id")

    if not task_id:
        return False, "task_id ausente"

    post_response = (
        supabase.table("instagram_posts")
        .select("id,status")
        .eq("manus_task_id", task_id)
        .limit(1)
        .execute()
    )
    if not post_response.data:
        return True, "tarefa ignorada: task_id não pertence a esta pipeline"

    post_id = int(post_response.data[0]["id"])

    if event_type == "task_created":
        return True, "task_created registrado"

    if event_type == "task_stopped":
        stop_reason = detail.get("stop_reason")
        if stop_reason == "finish":
            try:
                messages = listar_mensagens_tarefa(task_id)
                resultado = _montar_resultado_manus(messages)
                supabase.table("instagram_posts").update({
                    "status": "ready",
                    "manus_result": resultado,
                    "updated_at": _agora(),
                }).eq("id", post_id).execute()
                return True, "lote pronto"
            except Exception as exc:
                supabase.table("instagram_posts").update({
                    "status": "error",
                    "error": str(exc)[:4000],
                    "updated_at": _agora(),
                }).eq("id", post_id).execute()
                return False, "falha ao recuperar resultado Manus"

        supabase.table("instagram_posts").update({
            "status": "error",
            "error": detail.get("message") or "Manus interrompeu a tarefa.",
            "updated_at": _agora(),
        }).eq("id", post_id).execute()
        return False, "tarefa Manus não concluída"

    return True, "evento ignorado"
