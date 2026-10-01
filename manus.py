import json
import logging
import os
import re
import time
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


def _referencias_tarefas_anteriores() -> list[str]:
    """Retorna tarefas antigas concluídas para o Manus reutilizar o padrão visual."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/instagram_posts",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            params={
                "bot_id": f"eq.{BOT_ID}",
                "status": "in.(ready,published)",
                "select": "id,manus_task_id",
                "manus_task_id": "not.is.null",
                "order": "id.desc",
                "limit": "10",
            },
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json()
        referencias = []
        for row in rows if isinstance(rows, list) else []:
            task_id = str(row.get("manus_task_id") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9]{22}", task_id) and task_id not in referencias:
                referencias.append(task_id)
            if len(referencias) >= 3:
                break
        logger.info("Referências visuais de carrosséis anteriores carregadas: %s", len(referencias))
        return referencias
    except Exception:
        logger.exception("Não foi possível carregar referências visuais anteriores; seguindo sem elas.")
        return []


def criar_tarefa_carrossel(
    produtos: list[dict[str, Any]],
    post_id: int,
    marketplace: str | None = None,
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

    def detectar_marketplace(produto: dict[str, Any]) -> str:
        origem = str(produto.get("marketplace") or "").strip().lower()
        if origem in {"shopee", "mercadolivre"}:
            return origem
        link = str(
            produto.get("link")
            or produto.get("affiliateLink")
            or produto.get("manualAffiliateLink")
            or ""
        ).lower()
        if any(
            dominio in link
            for dominio in ("mercadolivre.com.br", "mercadolibre.com", "meli.la")
        ):
            return "mercadolivre"
        return "shopee"

    plataformas = {
        str(marketplace or "").strip().lower()
    } if str(marketplace or "").strip().lower() in {"shopee", "mercadolivre"} else {
        detectar_marketplace(produto) for produto in produtos
    }

    if len(plataformas) != 1:
        raise ManusAPIError(
            "CAROUSEL_MIXED_MARKETPLACES: "
            "um lote não pode misturar Shopee e Mercado Livre."
        )

    marketplace_final = next(iter(plataformas))
    plataforma_label = (
        "MERCADO LIVRE"
        if marketplace_final == "mercadolivre"
        else "SHOPEE"
    )

    # =========================================================
    # FONTE ÚNICA DO PROMPT
    # =========================================================

    prompt_base = _prompt_salvo()

    # =========================================================
    # PROMPT FINAL ENVIADO AO MANUS
    # =========================================================

    # O prompt salvo no Supabase já contém as regras editoriais e visuais completas.
    # Evitamos repetir essas regras aqui para manter a mensagem abaixo do limite
    # aproximado de 5.000 tokens da API do Manus.
    prompt_final = f"""
{prompt_base}

EXECUÇÃO DO LOTE #{post_id} — PLATAFORMA: {plataforma_label}
REFERÊNCIA VISUAL OBRIGATÓRIA: consulte as tarefas anteriores anexadas como referências. Use as imagens finais dessas tarefas como modelo visual canônico da Raposa Caçadora. Reproduza o mesmo sistema visual: composição e enquadramento, paleta, fundos, iluminação, tipografia, hierarquia e posição dos textos, margens, escala do produto, tratamento fotográfico, elementos gráficos, assinatura/watermark e consistência entre capa e slides. Não invente uma nova direção artística, não redesenhe a marca e não copie os textos nem os produtos antigos; reutilize somente o padrão visual.
Crie exatamente 6 imagens: 1 capa + 5 slides de produto, em formato vertical 4:5 (1080x1350).
Use somente os 5 produtos abaixo, na ordem fornecida. Preserve a identidade visual da Raposa Caçadora e as características reais de cada produto. Use a imagem correspondente de cada produto quando houver image_url. Não invente dados, preços, descontos, benefícios ou características. Não misture plataformas.
Se as tarefas de referência não estiverem acessíveis, mantenha rigorosamente o padrão editorial já descrito no prompt salvo: estética Pinterest/cozy quando compatível com o tema, acabamento premium, composição limpa, identidade Raposa Caçadora consistente e mesma linguagem visual em todos os slides.
Retorne a categoria, subcategoria, conceito, legenda completa e os 5 slides com position, product_id, headline, benefit e asset_url.

DADOS DOS 5 PRODUTOS:
{json.dumps(produtos_publicos, ensure_ascii=False, separators=(",", ":"))}
""".strip()

    # Estimativa conservadora para evitar HTTP 400 por mensagem longa.
    estimated_tokens = max(len(prompt_final) / 3.0, len(prompt_final.split()) * 1.5)
    logger.info(
        "Tamanho do pedido Manus para lote #%s: %s caracteres, estimativa %.0f tokens.",
        post_id, len(prompt_final), estimated_tokens,
    )
    if estimated_tokens > 4700:
        raise ManusAPIError(
            "MANUS_PROMPT_TOO_LONG: mensagem estimada em "
            f"{estimated_tokens:.0f} tokens; limite preventivo de 4700. "
            "Reduza o prompt editorial salvo em bot_settings.instagram_prompt."
        )

    referencias_visuais = _referencias_tarefas_anteriores()
    payload = {
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": prompt_final,
                    "visibility": "visible",
                }
            ],
            **({"task_references": referencias_visuais} if referencias_visuais else {}),
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

    ultimo_erro: Exception | None = None
    for tentativa in range(3):
        try:
            response = requests.get(
                f"{MANUS_API_URL}/v2/task.listMessages",
                headers={"x-manus-api-key": MANUS_API_KEY},
                params={"task_id": task_id, "order": "desc", "limit": 200},
                timeout=60,
            )
            if response.status_code >= 500 and tentativa < 2:
                logger.warning(
                    "Manus task.listMessages respondeu HTTP %s; nova tentativa %s/3.",
                    response.status_code, tentativa + 2,
                )
                time.sleep(1.5 * (tentativa + 1))
                continue
            return _parse_response(response, "task.listMessages")
        except requests.exceptions.RequestException as exc:
            ultimo_erro = exc
            if tentativa >= 2:
                raise
            logger.warning(
                "Falha transitória em task.listMessages (%s); nova tentativa %s/3.",
                exc, tentativa + 2,
            )
            time.sleep(1.5 * (tentativa + 1))
    raise ManusAPIError(f"MANUS_MESSAGES_UNAVAILABLE: {ultimo_erro}")
