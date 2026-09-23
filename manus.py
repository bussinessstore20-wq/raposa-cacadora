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
    return {
        "Content-Type": "application/json",
        "x-manus-api-key": MANUS_API_KEY,
    }


def criar_tarefa_carrossel(produtos: list[dict[str, Any]], post_id: int) -> dict[str, Any]:
    """Cria a tarefa Manus para preparar o carrossel.

    Nesta primeira etapa a Manus apenas gera/organiza o conteúdo e os assets
    solicitados. A publicação no Instagram será adicionada na próxima etapa.
    """
    if not produtos:
        raise ManusAPIError("Nenhum produto disponível para a tarefa.")

    produtos_publicos = []
    for produto in produtos:
        produtos_publicos.append({
            "id": produto.get("id"),
            "name": produto.get("productName") or produto.get("product_name") or "Produto",
            "price": produto.get("priceMin") or produto.get("price") or 0,
            "discount_rate": produto.get("priceDiscountRate") or 0,
            "rating": produto.get("ratingStar") or 0,
            "sales": produto.get("sales") or 0,
            "shop_name": produto.get("shopName") or "Loja Shopee",
            "image_url": produto.get("imageUrl") or produto.get("image_url") or "",
            "affiliate_url": produto.get("affiliateLink") or produto.get("manualAffiliateLink") or produto.get("link") or "",
        })

    prompt = f"""
Você é o criador de conteúdo da Raposa Caçadora.

Crie um carrossel para Instagram com {len(produtos_publicos)} produtos da Shopee.

IMPORTANTE:
- Não invente preço, desconto, avaliação ou características que não estejam nos dados.
- Identifique automaticamente a categoria/tema dos produtos.
- Crie 1 capa + 1 slide para cada produto.
- O conteúdo deve ser em português do Brasil.
- A capa deve ser chamativa e deixar claro o tema do carrossel.
- Cada slide de produto deve destacar nome, preço e benefício principal usando somente os dados disponíveis.
- Gere também uma legenda curta para Instagram com CTA.
- Preserve os links de afiliado exatamente como recebidos.
- Se criar imagens/assets, mantenha o mesmo padrão visual em todos os slides.

Retorne ao final um resumo estruturado contendo:
1. categoria identificada;
2. legenda;
3. ordem dos slides;
4. URLs/identificadores dos assets gerados, quando existirem.

ID interno do lote: {post_id}

Dados dos produtos:
{json.dumps(produtos_publicos, ensure_ascii=False, indent=2)}
""".strip()

    payload = {
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                    "visibility": "visible",
                }
            ]
        }
    }

    response = requests.post(
        f"{MANUS_API_URL}/v2/task.create",
        headers=_headers(),
        json=payload,
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(
            f"Resposta inválida da Manus (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(
            f"Erro ao criar tarefa Manus: {data}"
        )

    return data


def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{MANUS_API_URL}/v2/task.listMessages",
        headers=_headers(),
        params={"task_id": task_id, "order": "desc", "limit": 20},
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(
            f"Resposta inválida da Manus (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(
            f"Erro ao consultar mensagens Manus: {data}"
        )

    return data
