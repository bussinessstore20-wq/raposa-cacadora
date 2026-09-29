import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("raposa-cacadora.manus")
MANUS_API_URL = os.getenv("MANUS_API_URL", "https://api.manus.ai").rstrip("/")
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
BOT_ID = os.getenv("BOT_ID", os.getenv("FILA_ORIGEM", "raposa-cacadora")).strip()

DEFAULT_INSTAGRAM_PROMPT = """Você é o criador de conteúdo, copywriter e diretor visual da Raposa Caçadora. Crie um carrossel de Instagram no padrão editorial já definido pela Raposa Caçadora, mantendo o estilo visual e a linguagem da marca. Use exatamente os produtos fornecidos, sem inventar produtos, preços ou características. Crie exatamente 1 capa + 1 slide para cada produto, formato vertical 4:5 (1080x1350). A capa deve ter um título forte e chamativo relacionado ao conjunto. Cada slide deve destacar o produto com headline e benefício curto. A legenda deve ter gancho inicial, contexto, lista numerada dos produtos, CTA para curtir, seguir @raposacacadora e comentar EU QUERO, além de hashtags como #achadosshopee #shopee #raposacacadora. Não coloque URLs, links de afiliado ou links na ordem na legenda. Escreva em português do Brasil, com emojis moderados. A legenda deve citar todos os produtos na mesma ordem dos slides. Preserve a identidade de marca e o padrão do designer. Retorne categoria, caption e slides com position, product_id, headline, benefit e asset_url. Use a imagem correspondente do produto no asset_url quando houver URL de imagem nos dados."""

class ManusAPIError(RuntimeError):
    pass
@@ -27,22 +32,87 @@
        raise ManusAPIError(f"MANUS_HTTP_{response.status_code}: {error}")
    return data

def _prompt_salvo() -> str:
    """Lê o prompt operacional salvo no Supabase. O prompt do banco é a fonte de verdade."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase não configurado para leitura do prompt; usando prompt padrão.")
        return DEFAULT_INSTAGRAM_PROMPT
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/bot_settings",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            params={"bot_id": f"eq.{BOT_ID}", "select": "instagram_prompt", "limit": "1"},
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json()
        prompt = str(rows[0].get("instagram_prompt") or "").strip() if rows else ""
        if prompt:
            return prompt
    except Exception:
        logger.exception("Falha ao ler instagram_prompt do Supabase; usando fallback.")
    return DEFAULT_INSTAGRAM_PROMPT

def criar_tarefa_carrossel(produtos: list[dict[str, Any]], post_id: int) -> dict[str, Any]:
    if not produtos:
        raise ManusAPIError("Nenhum produto disponível para a tarefa.")
    produtos_publicos=[{"id":p.get("id"),"name":p.get("productName") or p.get("product_name") or "Produto","price":p.get("priceMin") or p.get("price") or 0,"discount_rate":p.get("priceDiscountRate") or 0,"rating":p.get("ratingStar") or 0,"sales":p.get("sales") or 0,"shop_name":p.get("shopName") or "Loja Shopee","image_url":p.get("imageUrl") or p.get("image_url") or "","affiliate_url":p.get("affiliateLink") or p.get("manualAffiliateLink") or p.get("link") or ""} for p in produtos]
    prompt=f"Você é o criador visual e copywriter da Raposa Caçadora. Crie um carrossel vertical 4:5 (1080x1350) com {len(produtos_publicos)} produtos da Shopee. O carrossel deve ter EXATAMENTE 1 capa + 1 slide para cada produto. Preserve fielmente os produtos fornecidos. Retorne categoria, legenda completa e slides com position, product_id, headline, benefit e asset_url. ID interno do lote: {post_id}. DADOS: {json.dumps(produtos_publicos,ensure_ascii=False,indent=2)}"
    payload={"message":{"content":[{"type":"text","text":prompt,"visibility":"visible"}]},"title":f"Carrossel Instagram - lote {post_id}","hide_in_task_list":True,"structured_output_schema":{"type":"object","properties":{"category":{"type":"string"},"caption":{"type":"string"},"slides":{"type":"array","items":{"type":"object","properties":{"position":{"type":"integer"},"product_id":{"type":"integer"},"headline":{"type":"string"},"benefit":{"type":"string"},"asset_url":{"type":"string"}},"required":["position","product_id","headline","benefit","asset_url"],"additionalProperties":False}}},"required":["category","caption","slides"],"additionalProperties":False}}
    response=requests.post(f"{MANUS_API_URL}/v2/task.create",headers=_headers(),json=payload,timeout=60)
    return _parse_response(response, "task.create")
    produtos_publicos = [
        {
            "id": p.get("id"),
            "name": p.get("productName") or p.get("product_name") or "Produto",
            "price": p.get("priceMin") or p.get("price") or 0,
            "discount_rate": p.get("priceDiscountRate") or 0,
            "rating": p.get("ratingStar") or 0,
            "sales": p.get("sales") or 0,
            "shop_name": p.get("shopName") or "Loja Shopee",
            "image_url": p.get("imageUrl") or p.get("image_url") or "",
            "affiliate_url": p.get("affiliateLink") or p.get("manualAffiliateLink") or p.get("link") or "",
        }
        for p in produtos
    ]
    prompt_base = _prompt_salvo()
    prompt = f"""{prompt_base}\n\nINSTRUÇÕES DA EXECUÇÃO ATUAL:\n- ID interno do lote: {post_id}\n- Quantidade de produtos: {len(produtos_publicos)}\n- Gere exatamente 1 capa + {len(produtos_publicos)} slides de produto.\n- Preserve a ordem dos product_id fornecidos.\n- Não altere, substitua ou invente produtos.\n\nDADOS DOS PRODUTOS:\n{json.dumps(produtos_publicos, ensure_ascii=False, indent=2)}"""
    payload = {
        "message": {"content": [{"type": "text", "text": prompt, "visibility": "visible"}]},
        "title": f"Carrossel Instagram - lote {post_id}",
        "hide_in_task_list": True,
        "structured_output_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "caption": {"type": "string"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {"type": "integer"},
                            "product_id": {"type": "integer"},
                            "headline": {"type": "string"},
                            "benefit": {"type": "string"},
                            "asset_url": {"type": "string"},
                        },
                        "required": ["position", "product_id", "headline", "benefit", "asset_url"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["category", "caption", "slides"],
            "additionalProperties": False,
        },
    }
    response = requests.post(f"{MANUS_API_URL}/v2/task.create", headers=_headers(), json=payload, timeout=60)
    data = _parse_response(response, "task.create")
    logger.info("Tarefa Manus criada para lote #%s usando prompt salvo do Supabase.", post_id)
    return data

def detalhar_tarefa(task_id: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    response=requests.get(
    response = requests.get(
        f"{MANUS_API_URL}/v2/task.detail",
        headers={"x-manus-api-key": MANUS_API_KEY},
        params={"task_id":task_id},
        params={"task_id": task_id},
        timeout=60,
    )
    data = _parse_response(response, "task.detail")
@@ -51,23 +121,30 @@
        raise ManusAPIError("MANUS_TASK_NOT_FOUND: task.detail não retornou a tarefa.")
    returned_id = str(task.get("id") or task.get("task_id") or "").strip()
    if returned_id and returned_id != task_id:
        raise ManusAPIError(
            f"MANUS_TASK_MISMATCH: esperado={task_id} recebido={returned_id}"
        )
        raise ManusAPIError(f"MANUS_TASK_MISMATCH: esperado={task_id} recebido={returned_id}")
    return data

def enviar_mensagem_tarefa(task_id: str, content: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    if not content.strip():
        raise ManusAPIError("Mensagem Manus vazia.")
    # Valida primeiro a existência/acesso à tarefa. Isso evita mascarar 404/403 como erro genérico.
    detalhar_tarefa(task_id)
    response=requests.post(f"{MANUS_API_URL}/v2/task.sendMessage",headers=_headers(),json={"task_id":task_id,"message":{"content":content.strip()}},timeout=60)
    response = requests.post(
        f"{MANUS_API_URL}/v2/task.sendMessage",
        headers=_headers(),
        json={"task_id": task_id, "message": {"content": content.strip()}},
        timeout=60,
    )
    return _parse_response(response, "task.sendMessage")

def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    response=requests.get(f"{MANUS_API_URL}/v2/task.listMessages",headers={"x-manus-api-key": MANUS_API_KEY},params={"task_id":task_id,"order":"desc","limit":200},timeout=60)
    response = requests.get(
        f"{MANUS_API_URL}/v2/task.listMessages",
        headers={"x-manus-api-key": MANUS_API_KEY},
        params={"task_id": task_id, "order": "desc", "limit": 200},
        timeout=60,
    )
    return _parse_response(response, "task.listMessages")
