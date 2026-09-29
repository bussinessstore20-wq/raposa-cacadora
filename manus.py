import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("raposa-cacadora.manus")

MANUS_API_URL = os.getenv(
    "MANUS_API_URL",
    "https://api.manus.ai",
).rstrip("/")

MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip().rstrip("/")

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()

BOT_ID = os.getenv(
    "BOT_ID",
    os.getenv("FILA_ORIGEM", "raposa-cacadora"),
).strip()


class ManusAPIError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not MANUS_API_KEY:
        raise ManusAPIError(
            "MANUS_API_KEY não configurada."
        )

    return {
        "Content-Type": "application/json",
        "x-manus-api-key": MANUS_API_KEY,
    }


def _parse_response(
    response: requests.Response,
    operation: str,
) -> dict[str, Any]:

    try:
        data = response.json()

    except ValueError as exc:
        raise ManusAPIError(
            f"MANUS_HTTP_{response.status_code}: "
            f"resposta não-JSON em {operation}"
        ) from exc

    if response.status_code >= 400:
        error = (
            data.get("error")
            if isinstance(data, dict)
            else data
        )

        raise ManusAPIError(
            f"MANUS_HTTP_{response.status_code}: {error}"
        )

    if isinstance(data, dict) and data.get("ok") is False:
        raise ManusAPIError(
            f"MANUS_ERROR: {data.get('error')}"
        )

    return data


def _prompt_salvo() -> str:
    """
    Busca o prompt operacional salvo no Supabase.

    O prompt do Supabase é a fonte de verdade.
    Não existe fallback silencioso para outro prompt.
    """

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ManusAPIError(
            "SUPABASE_PROMPT_UNAVAILABLE: "
            "SUPABASE_URL/SUPABASE_KEY não configurados."
        )

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/bot_settings",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            params={
                "bot_id": f"eq.{BOT_ID}",
                "select": "instagram_prompt",
                "limit": "1",
            },
            timeout=20,
        )

        response.raise_for_status()

        rows = response.json()

        if not rows:
            raise ManusAPIError(
                "SUPABASE_PROMPT_EMPTY: "
                f"nenhum prompt encontrado para bot_id={BOT_ID}."
            )

        prompt = str(
            rows[0].get("instagram_prompt") or ""
        ).strip()

        if not prompt:
            raise ManusAPIError(
                "SUPABASE_PROMPT_EMPTY: "
                f"instagram_prompt vazio para bot_id={BOT_ID}."
            )

        logger.info(
            "Prompt do Supabase carregado para bot=%s",
            BOT_ID,
        )

        return prompt

    except ManusAPIError:
        raise

    except Exception as exc:
        logger.exception(
            "Falha ao ler instagram_prompt do Supabase."
        )

        raise ManusAPIError(
            f"SUPABASE_PROMPT_READ_FAILED: {exc}"
        ) from exc


def _normalizar_produto(
    produto: dict[str, Any],
) -> dict[str, Any]:

    return {
        "id": produto.get("id"),

        "name": (
            produto.get("productName")
            or produto.get("product_name")
            or "Produto"
        ),

        "price": (
            produto.get("priceMin")
            or produto.get("price")
            or 0
        ),

        "discount_rate": (
            produto.get("priceDiscountRate")
            or 0
        ),

        "rating": (
            produto.get("ratingStar")
            or 0
        ),

        "sales": (
            produto.get("sales")
            or 0
        ),

        "shop_name": (
            produto.get("shopName")
            or "Loja Shopee"
        ),

        "image_url": (
            produto.get("imageUrl")
            or produto.get("image_url")
            or ""
        ),

        "affiliate_url": (
            produto.get("affiliateLink")
            or produto.get("manualAffiliateLink")
            or produto.get("link")
            or ""
        ),
    }


def criar_tarefa_carrossel(
    produtos: list[dict[str, Any]],
    post_id: int,
) -> dict[str, Any]:

    if not produtos:
        raise ManusAPIError(
            "Nenhum produto disponível para a tarefa."
        )

    if len(produtos) != 5:
        raise ManusAPIError(
            "CAROUSEL_REQUIRES_5_PRODUCTS: "
            f"recebido={len(produtos)}"
        )

    produtos_publicos = [
        _normalizar_produto(produto)
        for produto in produtos
    ]

    # =========================================================
    # FONTE ÚNICA DO PROMPT
    # =========================================================

    prompt_base = _prompt_salvo()

    # =========================================================
    # PROMPT FINAL ENVIADO AO MANUS
    # =========================================================

    prompt_final = f"""
{prompt_base}

INSTRUÇÕES TÉCNICAS DA EXECUÇÃO

ID interno do lote:
{post_id}

QUANTIDADE

Use exatamente os 5 produtos fornecidos abaixo.

Não invente produtos.
Não substitua produtos.
Não misture produtos.
Não altere a ordem.

ESTRUTURA OBRIGATÓRIA

Crie exatamente:

1 capa
+
5 slides de produto

Total:
6 imagens.

FORMATO

Formato vertical 4:5.

1080x1350.

PRODUTOS

A posição de cada produto deve permanecer exatamente igual
à ordem fornecida.

O product_id retornado deve corresponder ao produto utilizado
em cada slide.

IMAGENS

Sempre que existir image_url nos dados do produto,
utilize a imagem correspondente daquele produto.

Não associe a imagem de um produto a outro.

PRESERVAÇÃO DO PRODUTO

Não altere artificialmente:

- formato;
- cor;
- material;
- acabamento;
- características;
- modelo;
- quantidade;
- identidade visual do produto.

O produto precisa permanecer reconhecível.

DADOS DOS PRODUTOS

{json.dumps(
    produtos_publicos,
    ensure_ascii=False,
    indent=2,
)}
""".strip()

    payload = {
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": prompt_final,
                    "visibility": "visible",
                }
            ]
        },

        "title": (
            f"Carrossel Instagram - lote {post_id}"
        ),

        "hide_in_task_list": True,

        "structured_output_schema": {
            "type": "object",

            "properties": {

                "category": {
                    "type": "string",
                },

                "subcategory": {
                    "type": "string",
                },

                "concept": {
                    "type": "string",
                },

                "caption": {
                    "type": "string",
                },

                "slides": {
                    "type": "array",

                    "minItems": 5,
                    "maxItems": 5,

                    "items": {
                        "type": "object",

                        "properties": {

                            "position": {
                                "type": "integer",
                            },

                            "product_id": {
                                "type": "integer",
                            },

                            "headline": {
                                "type": "string",
                            },

                            "benefit": {
                                "type": "string",
                            },

                            "asset_url": {
                                "type": "string",
                            },
                        },

                        "required": [
                            "position",
                            "product_id",
                            "headline",
                            "benefit",
                            "asset_url",
                        ],

                        "additionalProperties": False,
                    },
                },
            },

            "required": [
                "category",
                "subcategory",
                "concept",
                "caption",
                "slides",
            ],

            "additionalProperties": False,
        },
    }

    logger.info(
        "Criando tarefa Manus para lote #%s "
        "usando prompt salvo no Supabase.",
        post_id,
    )

    response = requests.post(
        f"{MANUS_API_URL}/v2/task.create",
        headers=_headers(),
        json=payload,
        timeout=60,
    )

    data = _parse_response(
        response,
        "task.create",
    )

    logger.info(
        "Tarefa Manus criada com sucesso para lote #%s.",
        post_id,
    )

    return data


def detalhar_tarefa(
    task_id: str,
) -> dict[str, Any]:

    if not task_id:
        raise ManusAPIError(
            "task_id ausente."
        )

    response = requests.get(
        f"{MANUS_API_URL}/v2/task.detail",
        headers={
            "x-manus-api-key": MANUS_API_KEY,
        },
        params={
            "task_id": task_id,
        },
        timeout=60,
    )

    data = _parse_response(
        response,
        "task.detail",
    )

    task = data.get("task")

    if not isinstance(task, dict):
        raise ManusAPIError(
            "MANUS_TASK_NOT_FOUND: "
            "task.detail não retornou a tarefa."
        )

    returned_id = str(
        task.get("id")
        or task.get("task_id")
        or ""
    ).strip()

    if returned_id and returned_id != task_id:
        raise ManusAPIError(
            "MANUS_TASK_MISMATCH: "
            f"esperado={task_id} "
            f"recebido={returned_id}"
        )

    return data


def enviar_mensagem_tarefa(
    task_id: str,
    content: str,
) -> dict[str, Any]:

    if not task_id:
        raise ManusAPIError(
            "task_id ausente."
        )

    if not content.strip():
        raise ManusAPIError(
            "Mensagem Manus vazia."
        )

    # Primeiro confirma que a tarefa existe.
    detalhar_tarefa(task_id)

    response = requests.post(
        f"{MANUS_API_URL}/v2/task.sendMessage",
        headers=_headers(),
        json={
            "task_id": task_id,
            "message": {
                "content": content.strip(),
            },
        },
        timeout=60,
    )

    return _parse_response(
        response,
        "task.sendMessage",
    )


def listar_mensagens_tarefa(
    task_id: str,
) -> dict[str, Any]:

    if not task_id:
        raise ManusAPIError(
            "task_id ausente."
        )

    response = requests.get(
        f"{MANUS_API_URL}/v2/task.listMessages",
        headers={
            "x-manus-api-key": MANUS_API_KEY,
        },
        params={
            "task_id": task_id,
            "order": "desc",
            "limit": 200,
        },
        timeout=60,
    )

    return _parse_response(
        response,
        "task.listMessages",
    )
