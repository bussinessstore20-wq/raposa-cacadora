import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlparse, parse_qs

import requests


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_GRAPHQL_URL = os.getenv(
    "SHOPEE_GRAPHQL_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
).strip()


# ============================================================
# ERRO DA API
# ============================================================

class ShopeeAPIError(Exception):
    """Erro relacionado à API de Afiliados da Shopee."""


# ============================================================
# CREDENCIAIS
# ============================================================

def _obter_credenciais():

    app_id = os.getenv(
        "SHOPEE_APP_ID",
        ""
    ).strip()

    secret = os.getenv(
        "SHOPEE_SECRET",
        ""
    ).strip()

    if not app_id:

        raise ShopeeAPIError(
            "A variável SHOPEE_APP_ID "
            "não está configurada."
        )

    if not secret:

        raise ShopeeAPIError(
            "A variável SHOPEE_SECRET "
            "não está configurada."
        )

    return (
        app_id,
        secret
    )


# ============================================================
# RESOLVER LINK DA SHOPEE
# ============================================================

def resolver_link(
    link: str
):

    link = link.strip()

    if not link:

        raise ShopeeAPIError(
            "Link da Shopee está vazio."
        )

    logger.info(
        "Resolvendo link da Shopee: %s",
        link
    )

    try:

        response = requests.get(
            link,
            allow_redirects=True,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/130.0.0.0 "
                    "Safari/537.36"
                ),
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "image/avif,"
                    "image/webp,"
                    "*/*;q=0.8"
                ),
                "Accept-Language": (
                    "pt-BR,pt;q=0.9"
                ),
            }
        )

    except requests.RequestException as erro:

        raise ShopeeAPIError(
            "Erro ao resolver link da Shopee: "
            f"{erro}"
        ) from erro

    logger.info(
        "HTTP ao resolver link: %d",
        response.status_code
    )

    if response.status_code >= 400:

        raise ShopeeAPIError(
            "Shopee retornou HTTP "
            f"{response.status_code} "
            "ao resolver o link."
        )

    url_final = response.url

    logger.info(
        "URL final resolvida: %s",
        url_final
    )

    if not url_final:

        raise ShopeeAPIError(
            "A Shopee não retornou uma "
            "URL final."
        )

    return url_final


# ============================================================
# EXTRAIR SHOP ID / ITEM ID
# ============================================================

def extrair_ids_da_url(
    url: str
):

    logger.info(
        "Extraindo IDs da URL..."
    )

    if not url:

        raise ShopeeAPIError(
            "URL final vazia."
        )

    parsed = urlparse(
        url
    )

    caminho = parsed.path.strip(
        "/"
    )

    logger.info(
        "Caminho da URL: %s",
        caminho
    )

    # ========================================================
    # FORMATO:
    #
    # /produto-i.123456.789012
    # ========================================================

    match = re.search(
        r"-i\.(\d+)\.(\d+)",
        caminho,
        re.IGNORECASE
    )

    if match:

        shop_id = match.group(1)
        item_id = match.group(2)

        logger.info(
            "IDs encontrados no formato -i: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # ========================================================
    # FORMATO:
    #
    # /product/123456/789012
    # ========================================================

    match = re.search(
        r"(?:^|/)product/(\d+)/(\d+)(?:/|$)",
        caminho,
        re.IGNORECASE
    )

    if match:

        shop_id = match.group(1)
        item_id = match.group(2)

        logger.info(
            "IDs encontrados no formato product: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # ========================================================
    # FORMATO:
    #
    # /opaanlp/1022739284/20299032787
    # ========================================================

    partes = [
        parte
        for parte in caminho.split("/")
        if parte
    ]

    logger.info(
        "Segmentos encontrados: %s",
        partes
    )

    for i in range(
        len(partes) - 1
    ):

        primeiro = partes[i]
        segundo = partes[i + 1]

        if (
            primeiro.isdigit()
            and segundo.isdigit()
        ):

            shop_id = primeiro
            item_id = segundo

            logger.info(
                "IDs encontrados no caminho: "
                "shopId=%s | itemId=%s",
                shop_id,
                item_id
            )

            return (
                shop_id,
                item_id
            )

    # ========================================================
    # FORMATO:
    #
    # ?shopid=123456&itemid=789012
    # ========================================================

    parametros = parse_qs(
        parsed.query
    )

    shop_values = (
        parametros.get("shopid")
        or parametros.get("shopId")
        or parametros.get("shop_id")
    )

    item_values = (
        parametros.get("itemid")
        or parametros.get("itemId")
        or parametros.get("item_id")
    )

    if (
        shop_values
        and item_values
    ):

        shop_id = shop_values[0]
        item_id = item_values[0]

        logger.info(
            "IDs encontrados nos parâmetros: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # ========================================================
    # ÚLTIMA TENTATIVA
    # ========================================================

    numeros = re.findall(
        r"\d+",
        caminho
    )

    if len(numeros) >= 2:

        shop_id = numeros[-2]
        item_id = numeros[-1]

        logger.info(
            "IDs encontrados por expressão numérica: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # ========================================================
    # ERRO
    # ========================================================

    logger.error(
        "Não foi possível extrair shopId/itemId."
    )

    logger.error(
        "URL analisada: %s",
        url
    )

    logger.error(
        "Caminho analisado: %s",
        caminho
    )

    raise ShopeeAPIError(
        "Não foi possível encontrar "
        "shopId/itemId na URL final "
        "da Shopee."
    )


# ============================================================
# ASSINATURA
# ============================================================

def _gerar_assinatura(
    app_id,
    secret,
    timestamp,
    payload
):

    texto_assinatura = (
        f"{app_id}"
        f"{timestamp}"
        f"{payload}"
        f"{secret}"
    )

    assinatura = hashlib.sha256(
        texto_assinatura.encode(
            "utf-8"
        )
    ).hexdigest()

    return assinatura


# ============================================================
# GRAPHQL
# ============================================================

def _graphql(
    query: str,
    variables: dict | None = None
):

    app_id, secret = (
        _obter_credenciais()
    )

    # ========================================================
    # PAYLOAD
    # ========================================================

    payload_obj = {
        "query": query
    }

    if variables is not None:

        payload_obj[
            "variables"
        ] = variables

    payload = json.dumps(
        payload_obj,
        ensure_ascii=False,
        separators=(
            ",",
            ":"
        )
    )

    # ========================================================
    # TIMESTAMP
    # ========================================================

    timestamp = int(
        time.time()
    )

    # ========================================================
    # ASSINATURA
    # ========================================================

    assinatura = _gerar_assinatura(
        app_id=app_id,
        secret=secret,
        timestamp=timestamp,
        payload=payload
    )

    authorization = (
        f"SHA256 "
        f"Credential={app_id}, "
        f"Timestamp={timestamp}, "
        f"Signature={assinatura}"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
    }

    logger.info(
        "Enviando requisição GraphQL para: %s",
        SHOPEE_GRAPHQL_URL
    )

    logger.debug(
        "GraphQL payload: %s",
        payload
    )

    try:

        response = requests.post(
            SHOPEE_GRAPHQL_URL,
            data=payload.encode(
                "utf-8"
            ),
            headers=headers,
            timeout=30
        )

    except requests.RequestException as erro:

        raise ShopeeAPIError(
            "Erro de conexão com a Shopee: "
            f"{erro}"
        ) from erro

    logger.info(
        "Shopee GraphQL HTTP %d",
        response.status_code
    )

    # ========================================================
    # HTTP
    # ========================================================

    if response.status_code != 200:

        logger.error(
            "Resposta HTTP da Shopee: %s",
            response.text[:2000]
        )

        raise ShopeeAPIError(
            "Shopee respondeu HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    # ========================================================
    # JSON
    # ========================================================

    try:

        resultado = response.json()

    except ValueError as erro:

        logger.error(
            "Resposta não JSON da Shopee: %s",
            response.text[:2000]
        )

        raise ShopeeAPIError(
            "A Shopee retornou uma resposta "
            "que não é JSON."
        ) from erro

    # ========================================================
    # ERROS GRAPHQL
    # ========================================================

    if resultado.get(
        "errors"
    ):

        erros = resultado.get(
            "errors"
        )

        logger.error(
            "Erro GraphQL da Shopee: %s",
            json.dumps(
                erros,
                ensure_ascii=False
            )
        )

        raise ShopeeAPIError(
            "A API da Shopee retornou "
            "erros GraphQL: "
            + json.dumps(
                erros,
                ensure_ascii=False
            )
        )

    return resultado


# ============================================================
# QUERY DO PRODUTO
# ============================================================
#
# IMPORTANTE:
#
# Os IDs são inseridos diretamente na query.
#
# NÃO usamos:
#
#     $shopId: Int64
#     $itemId: Int64
#
# Isso elimina o problema de coerção de variável do
# GraphQL da Shopee.
#
# ============================================================

def _montar_query_produto(
    shop_id: int,
    item_id: int
):

    return f"""
query ProductOffer {{
    productOfferV2(
        shopId: {shop_id},
        itemId: {item_id},
        page: 1,
        limit: 1
    ) {{
        nodes {{
            productName
            itemId
            commissionRate
            commission
            price
            sales
            imageUrl
            shopName
            productLink
            offerLink
            periodStartTime
            periodEndTime
            priceMin
            priceMax
            productCatIds
            ratingStar
            priceDiscountRate
            shopId
            shopType
            sellerCommissionRate
            shopeeCommissionRate
        }}

        pageInfo {{
            page
            limit
            hasNextPage
        }}
    }}
}}
"""


# ============================================================
# QUERY MÍNIMA DE DIAGNÓSTICO
# ============================================================
#
# Se a query completa falhar, podemos usar somente esses
# campos para separar problema de filtro de problema de
# algum campo do retorno.
#
# ============================================================

def _montar_query_produto_minima(
    shop_id: int,
    item_id: int
):

    return f"""
query ProductOffer {{
    productOfferV2(
        shopId: {shop_id},
        itemId: {item_id},
        page: 1,
        limit: 1
    ) {{
        nodes {{
            itemId
            productName
            shopId
        }}

        pageInfo {{
            page
            limit
            hasNextPage
        }}
    }}
}}
"""


# ============================================================
# BUSCAR PRODUTO POR ID
# ============================================================

def buscar_produto_por_ids(
    shop_id,
    item_id
):

    logger.info(
        "Consultando produto: "
        "shopId=%s | itemId=%s",
        shop_id,
        item_id
    )

    # ========================================================
    # CONVERTER
    # ========================================================

    try:

        shop_id_int = int(
            shop_id
        )

        item_id_int = int(
            item_id
        )

    except (
        TypeError,
        ValueError
    ) as erro:

        raise ShopeeAPIError(
            "shopId/itemId inválidos: "
            f"{shop_id}/{item_id}"
        ) from erro

    # ========================================================
    # VALIDAR
    # ========================================================

    if shop_id_int <= 0:

        raise ShopeeAPIError(
            f"shopId inválido: {shop_id_int}"
        )

    if item_id_int <= 0:

        raise ShopeeAPIError(
            f"itemId inválido: {item_id_int}"
        )

    logger.info(
        "IDs convertidos: "
        "shopId=%d | itemId=%d",
        shop_id_int,
        item_id_int
    )

    # ========================================================
    # MONTAR QUERY
    # ========================================================

    query = _montar_query_produto(
        shop_id=shop_id_int,
        item_id=item_id_int
    )

    logger.debug(
        "Query do produto: %s",
        query
    )

    # ========================================================
    # CONSULTAR SEM VARIABLES
    # ========================================================

    resultado = _graphql(
        query=query
    )

    # ========================================================
    # DATA
    # ========================================================

    data = (
        resultado.get(
            "data"
        )
        or {}
    )

    product_offer = (
        data.get(
            "productOfferV2"
        )
        or {}
    )

    nodes = (
        product_offer.get(
            "nodes"
        )
        or []
    )

    # ========================================================
    # NENHUM RESULTADO
    # ========================================================

    if not nodes:

        logger.warning(
            "Nenhum produto encontrado "
            "para shopId=%s itemId=%s",
            shop_id_int,
            item_id_int
        )

        return None

    # ========================================================
    # PRODUTO
    # ========================================================

    produto = nodes[0]

    # ========================================================
    # GARANTIR IDS
    # ========================================================

    if not produto.get(
        "shopId"
    ):

        produto[
            "shopId"
        ] = shop_id_int

    if not produto.get(
        "itemId"
    ):

        produto[
            "itemId"
        ] = item_id_int

    # ========================================================
    # LOG
    # ========================================================

    logger.info(
        "Produto encontrado: %s",
        produto.get(
            "productName",
            "Produto"
        )
    )

    logger.info(
        "Shop ID retornado: %s",
        produto.get(
            "shopId"
        )
    )

    logger.info(
        "Item ID retornado: %s",
        produto.get(
            "itemId"
        )
    )

    return produto


# ============================================================
# BUSCAR PRODUTO POR LINK
# ============================================================

def buscar_produto_por_link(
    link: str
):

    if not link:

        raise ShopeeAPIError(
            "Link vazio."
        )

    link = link.strip()

    # ========================================================
    # RESOLVER
    # ========================================================

    url_final = resolver_link(
        link
    )

    # ========================================================
    # EXTRAIR IDS
    # ========================================================

    shop_id, item_id = (
        extrair_ids_da_url(
            url_final
        )
    )

    logger.info(
        "IDs extraídos com sucesso: "
        "shopId=%s | itemId=%s",
        shop_id,
        item_id
    )

    # ========================================================
    # CONSULTAR
    # ========================================================

    produto = buscar_produto_por_ids(
        shop_id=shop_id,
        item_id=item_id
    )

    # ========================================================
    # NÃO ENCONTROU
    # ========================================================

    if not produto:

        raise ShopeeAPIError(
            "O produto não foi encontrado "
            "na API de Afiliados da Shopee."
        )

    # ========================================================
    # PRESERVAR LINK ORIGINAL
    # ========================================================

    produto[
        "manualAffiliateLink"
    ] = link

    produto[
        "affiliateLink"
    ] = link

    logger.info(
        "Link de afiliado original "
        "preservado para o botão."
    )

    return produto
