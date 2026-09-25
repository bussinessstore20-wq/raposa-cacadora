import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("raposa-cacadora.manus")
MANUS_API_URL = os.getenv("MANUS_API_URL", "https://api.manus.ai").rstrip("/")
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()

class ManusAPIError(RuntimeError):
    pass

def _headers() -> dict[str, str]:
    if not MANUS_API_KEY:
        raise ManusAPIError("MANUS_API_KEY não configurada.")
    return {"Content-Type": "application/json", "x-manus-api-key": MANUS_API_KEY}

def _parse_response(response: requests.Response, operation: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(f"MANUS_HTTP_{response.status_code}: resposta não-JSON em {operation}") from exc
    if response.status_code >= 400 or not data.get("ok", True):
        error = data.get("error") if isinstance(data, dict) else data
        raise ManusAPIError(f"MANUS_HTTP_{response.status_code}: {error}")
    return data

def criar_tarefa_carrossel(produtos: list[dict[str, Any]], post_id: int) -> dict[str, Any]:
    if not produtos:
        raise ManusAPIError("Nenhum produto disponível para a tarefa.")
    produtos_publicos=[{"id":p.get("id"),"name":p.get("productName") or p.get("product_name") or "Produto","price":p.get("priceMin") or p.get("price") or 0,"discount_rate":p.get("priceDiscountRate") or 0,"rating":p.get("ratingStar") or 0,"sales":p.get("sales") or 0,"shop_name":p.get("shopName") or "Loja Shopee","image_url":p.get("imageUrl") or p.get("image_url") or "","affiliate_url":p.get("affiliateLink") or p.get("manualAffiliateLink") or p.get("link") or ""} for p in produtos]
    prompt=f"Você é o criador visual e copywriter da Raposa Caçadora. Crie um carrossel vertical 4:5 (1080x1350) com {len(produtos_publicos)} produtos da Shopee. O carrossel deve ter EXATAMENTE 1 capa + 1 slide para cada produto. Preserve fielmente os produtos fornecidos. Retorne categoria, legenda completa e slides com position, product_id, headline, benefit e asset_url. ID interno do lote: {post_id}. DADOS: {json.dumps(produtos_publicos,ensure_ascii=False,indent=2)}"
    payload={"message":{"content":[{"type":"text","text":prompt,"visibility":"visible"}]},"title":f"Carrossel Instagram - lote {post_id}","hide_in_task_list":True,"structured_output_schema":{"type":"object","properties":{"category":{"type":"string"},"caption":{"type":"string"},"slides":{"type":"array","items":{"type":"object","properties":{"position":{"type":"integer"},"product_id":{"type":"integer"},"headline":{"type":"string"},"benefit":{"type":"string"},"asset_url":{"type":"string"}},"required":["position","product_id","headline","benefit","asset_url"],"additionalProperties":False}}},"required":["category","caption","slides"],"additionalProperties":False}}
    response=requests.post(f"{MANUS_API_URL}/v2/task.create",headers=_headers(),json=payload,timeout=60)
    return _parse_response(response, "task.create")

def detalhar_tarefa(task_id: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    response=requests.get(
        f"{MANUS_API_URL}/v2/task.detail",
        headers={"x-manus-api-key": MANUS_API_KEY},
        params={"task_id":task_id},
        timeout=60,
    )
    data = _parse_response(response, "task.detail")
    task = data.get("task")
    if not isinstance(task, dict):
        raise ManusAPIError("MANUS_TASK_NOT_FOUND: task.detail não retornou a tarefa.")
    returned_id = str(task.get("id") or task.get("task_id") or "").strip()
    if returned_id and returned_id != task_id:
        raise ManusAPIError(
            f"MANUS_TASK_MISMATCH: esperado={task_id} recebido={returned_id}"
        )
    return data

def enviar_mensagem_tarefa(task_id: str, content: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    if not content.strip():
        raise ManusAPIError("Mensagem Manus vazia.")
    # Valida primeiro a existência/acesso à tarefa. Isso evita mascarar 404/403 como erro genérico.
    detalhar_tarefa(task_id)
    response=requests.post(f"{MANUS_API_URL}/v2/task.sendMessage",headers=_headers(),json={"task_id":task_id,"message":{"content":content.strip()}},timeout=60)
    return _parse_response(response, "task.sendMessage")

def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    response=requests.get(f"{MANUS_API_URL}/v2/task.listMessages",headers={"x-manus-api-key": MANUS_API_KEY},params={"task_id":task_id,"order":"desc","limit":200},timeout=60)
    return _parse_response(response, "task.listMessages")
