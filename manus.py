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


MANUS_REFERENCE_CAPA_B64 = """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"""
MANUS_REFERENCE_PRODUTO_B64 = """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"""

def _referencias_imagens_manuais() -> list[dict[str, str]]:
    """Retorna as referências visuais oficiais da Raposa Caçadora como anexos de imagem."""
    return [
        {
            "type": "file",
            "filename": "raposa-cacadora-capa-referencia.jpg",
            "file_data": f"data:image/jpeg;base64,{MANUS_REFERENCE_CAPA_B64}",
            "mime_type": "image/jpeg",
            "visibility": "visible",
        },
        {
            "type": "file",
            "filename": "raposa-cacadora-produto-referencia.jpg",
            "file_data": f"data:image/jpeg;base64,{MANUS_REFERENCE_PRODUTO_B64}",
            "mime_type": "image/jpeg",
            "visibility": "visible",
        },
    ]


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
REFERÊNCIAS VISUAIS OFICIAIS ANEXADAS: as duas imagens anexadas a esta tarefa são o padrão visual obrigatório da Raposa Caçadora. A primeira é a referência de CAPA; a segunda é a referência de SLIDE DE PRODUTO. Analise visualmente os anexos antes de criar qualquer imagem.
CAPA: reproduza a linguagem da referência de capa: fotografia lifestyle premium e fotorealista, ambiente aconchegante, tons quentes de bege/caramelo/marrom, iluminação natural dourada, composição editorial Pinterest, tipografia serifada elegante e grande, elementos decorativos delicados, caixa arredondada para a chamada da plataforma e assinatura @raposacacadora. Mantenha texto legível e hierarquia visual semelhante.
SLIDE DE PRODUTO: reproduza a linguagem da referência de produto: produto integrado em ambiente lifestyle realista, iluminação quente, fundo sofisticado e natural, composição limpa, produto como protagonista, tipografia serifada elegante na área inferior e amplo espaço visual.
As referências definem DESIGN, não conteúdo. NÃO copie o produto, textos ou objetos específicos das referências. Para cada lote, use exclusivamente a imagem real correspondente de cada produto como referência do produto anunciado. Não substitua o produto por um parecido.
O padrão visual deve ser aplicado também ao Mercado Livre; somente textos, hashtags e identidade da plataforma podem variar conforme as regras do prompt salvo.
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
                },
                *_referencias_imagens_manuais(),
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
        except ManusAPIError as exc:
            if str(exc).startswith(("MANUS_HTTP_500:", "MANUS_HTTP_502:", "MANUS_HTTP_503:", "MANUS_HTTP_504:")) and tentativa < 2:
                logger.warning(
                    "Manus task.listMessages falhou com erro 5xx (%s); nova tentativa %s/3.",
                    exc, tentativa + 2,
                )
                time.sleep(1.5 * (tentativa + 1))
                continue
            raise
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
