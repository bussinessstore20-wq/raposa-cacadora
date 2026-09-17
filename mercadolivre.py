import logging
import os
import re
from urllib.parse import urlparse, parse_qs

import requests


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

MERCADOLIVRE_API_URL = os.getenv(
    "MERCADOLIVRE_API_URL",
    "https://api.mercadolibre.com",
).strip()


# ============================================================
# ERRO DA API
# ============================================================

class MercadoLivreAPIError(Exception):
    """Erro relacionado à API do Mercado Livre."""


# ============================================================
# CREDENCIAL
# ============================================================

def _obter_access_token():

    access_token = os.getenv(
        "MERCADOLIVRE_ACCESS_TOKEN",
        "",
    ).strip()

    if not access_token:

        raise MercadoLivreAPIError(
            "A variável "
            "MERCADOLIVRE_ACCESS_TOKEN "
            "não está configurada."
        )

    return access_token


# ============================================================
# HEADERS DA API
# ============================================================

def _obter_headers():

    access_token = _obter_access_token()

    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/130.0.0.0 "
            "Safari/537.36"
        ),
    }


# ============================================================
# RESOLVER LINK DO MERCADO LIVRE
# ============================================================

def resolver_link(
    link: str
):

    link = link.strip()

    if not link:

        raise MercadoLivreAPIError(
            "Link do Mercado Livre está vazio."
        )

    logger.info(
        "Resolvendo link do Mercado Livre: %s",
        link,
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
            },
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao resolver link do Mercado Livre: "
            f"{erro}"
        ) from erro

    logger.info(
        "HTTP ao resolver link: %d",
        response.status_code,
    )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Mercado Livre retornou HTTP "
            f"{response.status_code} "
            "ao resolver o link."
        )

    url_final = response.url

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    if not url_final:

        raise MercadoLivreAPIError(
            "O Mercado Livre não retornou "
            "uma URL final."
        )

    return url_final


# ============================================================
# EXTRAIR ITEM ID DO MERCADO LIVRE
# ============================================================
#
# Exemplos aceitos:
#
# MLB1234567890
#
# https://produto.mercadolivre.com.br/MLB-1234567890-produto
#
# https://www.mercadolivre.com.br/.../MLB1234567890
#
# ?item_id=MLB1234567890
#
# ?itemId=MLB1234567890
#
# ============================================================

def extrair_item_id_da_url(
    url: str
):

    logger.info(
        "Extraindo Item ID do Mercado Livre..."
    )

    if not url:

        raise MercadoLivreAPIError(
            "URL final vazia."
        )

    parsed = urlparse(
        url
    )

    caminho = parsed.path.strip(
        "/"
    )

    query = parsed.query

    logger.info(
        "Caminho da URL: %s",
        caminho,
    )

    # ========================================================
    # 1. PADRÃO MLB1234567890
    # ========================================================

    match = re.search(
        r"\b(MLB\d{6,})\b",
        url,
        re.IGNORECASE,
    )

    if match:

        item_id = match.group(1).upper()

        logger.info(
            "Item ID encontrado: %s",
            item_id,
        )

        return item_id

    # ========================================================
    # 2. PADRÃO MLB-1234567890
    # ========================================================

    match = re.search(
        r"\bMLB[-_](\d{6,})\b",
        url,
        re.IGNORECASE,
    )

    if match:

        item_id = (
            "MLB"
            + match.group(1)
        ).upper()

        logger.info(
            "Item ID encontrado no formato "
            "MLB-XXXXXXXX: %s",
            item_id,
        )

        return item_id

    # ========================================================
    # 3. PARÂMETRO item_id
    # ========================================================

    parametros = parse_qs(
        query
    )

    item_values = (
        parametros.get("item_id")
        or parametros.get("itemId")
        or parametros.get("itemid")
    )

    if item_values:

        valor = item_values[0].strip()

        match = re.search(
            r"(MLB\d{6,})",
            valor,
            re.IGNORECASE,
        )

        if match:

            item_id = match.group(1).upper()

            logger.info(
                "Item ID encontrado nos parâmetros: %s",
                item_id,
            )

            return item_id

    # ========================================================
    # 4. ÚLTIMA TENTATIVA:
    # MLB + número
    # ========================================================

    match = re.search(
        r"MLB(\d{6,})",
        url,
        re.IGNORECASE,
    )

    if match:

        item_id = (
            "MLB"
            + match.group(1)
        ).upper()

        logger.info(
            "Item ID encontrado por expressão "
            "numérica: %s",
            item_id,
        )

        return item_id

    # ========================================================
    # ERRO
    # ========================================================

    logger.error(
        "Não foi possível extrair "
        "Item ID do Mercado Livre."
    )

    logger.error(
        "URL analisada: %s",
        url,
    )

    logger.error(
        "Caminho analisado: %s",
        caminho,
    )

    raise MercadoLivreAPIError(
        "Não foi possível encontrar o "
        "Item ID do Mercado Livre na URL final."
    )


# ============================================================
# EXTRAIR ITEM ID DE UM LINK SEM RESOLVER
# ============================================================
#
# Útil caso alguém passe diretamente:
#
# https://www.mercadolivre.com.br/MLB1234567890
#
# ============================================================

def extrair_item_id(
    valor: str
):

    if not valor:

        raise MercadoLivreAPIError(
            "Valor vazio para extração do Item ID."
        )

    valor = str(
        valor
    ).strip()

    match = re.search(
        r"\b(MLB\d{6,})\b",
        valor,
        re.IGNORECASE,
    )

    if match:

        return match.group(1).upper()

    match = re.search(
        r"\bMLB[-_](\d{6,})\b",
        valor,
        re.IGNORECASE,
    )

    if match:

        return (
            "MLB"
            + match.group(1)
        ).upper()

    return extrair_item_id_da_url(
        valor
    )


# ============================================================
# API DO MERCADO LIVRE
# ============================================================

def _api_get(
    endpoint: str,
    params: dict | None = None,
):

    url = (
        MERCADOLIVRE_API_URL.rstrip("/")
        + "/"
        + endpoint.lstrip("/")
    )

    headers = _obter_headers()

    logger.info(
        "Consultando Mercado Livre: %s",
        url,
    )

    if params:

        logger.debug(
            "Parâmetros: %s",
            params,
        )

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro de conexão com a API "
            "do Mercado Livre: "
            f"{erro}"
        ) from erro

    logger.info(
        "Mercado Livre HTTP %d",
        response.status_code,
    )

    # ========================================================
    # HTTP
    # ========================================================

    if response.status_code != 200:

        logger.error(
            "Resposta do Mercado Livre: %s",
            response.text[:2000],
        )

        try:

            erro_json = response.json()

        except ValueError:

            erro_json = None

        if erro_json:

            raise MercadoLivreAPIError(
                "Mercado Livre respondeu HTTP "
                f"{response.status_code}: "
                f"{erro_json}"
            )

        raise MercadoLivreAPIError(
            "Mercado Livre respondeu HTTP "
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
            "Resposta não JSON do Mercado Livre: %s",
            response.text[:2000],
        )

        raise MercadoLivreAPIError(
            "O Mercado Livre retornou uma "
            "resposta que não é JSON."
        ) from erro

    return resultado


# ============================================================
# QUERY DO PRODUTO
# ============================================================

def _consultar_item(
    item_id: str
):

    item_id = str(
        item_id
    ).strip().upper()

    if not re.fullmatch(
        r"MLB\d{6,}",
        item_id,
    ):

        raise MercadoLivreAPIError(
            f"Item ID inválido: {item_id}"
        )

    endpoint = (
        f"/items/{item_id}"
    )

    return _api_get(
        endpoint
    )


# ============================================================
# BUSCAR PRODUTO POR ID
# ============================================================

def buscar_produto_por_ids(
    item_id,
    shop_id=None,
):

    logger.info(
        "Consultando produto: "
        "itemId=%s",
        item_id,
    )

    # ========================================================
    # NORMALIZAR ITEM ID
    # ========================================================

    try:

        item_id_normalizado = extrair_item_id(
            str(item_id)
        )

    except (
        TypeError,
        ValueError,
        MercadoLivreAPIError,
    ) as erro:

        raise MercadoLivreAPIError(
            "Item ID inválido: "
            f"{item_id}"
        ) from erro

    logger.info(
        "Item ID normalizado: %s",
        item_id_normalizado,
    )

    # ========================================================
    # CONSULTAR API
    # ========================================================

    produto_api = _consultar_item(
        item_id_normalizado
    )

    if not produto_api:

        logger.warning(
            "Nenhum produto retornado "
            "para %s",
            item_id_normalizado,
        )

        return None

    # ========================================================
    # IDS
    # ========================================================

    item_id_retorno = (
        produto_api.get(
            "id"
        )
        or item_id_normalizado
    )

    seller_id = produto_api.get(
        "seller_id"
    )

    # ========================================================
    # IMAGEM PRINCIPAL
    # ========================================================

    image_url = None

    pictures = (
        produto_api.get(
            "pictures"
        )
        or []
    )

    if pictures:

        primeira_imagem = pictures[0]

        if isinstance(
            primeira_imagem,
            dict,
        ):

            image_url = (
                primeira_imagem.get(
                    "secure_url"
                )
                or primeira_imagem.get(
                    "url"
                )
            )

    # ========================================================
    # LINK DO PRODUTO
    # ========================================================

    product_link = (
        produto_api.get(
            "permalink"
        )
    )

    # ========================================================
    # PREÇO
    # ========================================================

    price = produto_api.get(
        "price"
    )

    original_price = produto_api.get(
        "original_price"
    )

    # ========================================================
    # DESCONTO
    # ========================================================

    price_discount_rate = None

    if (
        original_price
        and price
        and original_price > 0
        and price < original_price
    ):

        price_discount_rate = round(
            (
                (
                    original_price
                    - price
                )
                / original_price
            )
            * 100,
            2,
        )

    # ========================================================
    # VENDAS
    # ========================================================

    sales = (
        produto_api.get(
            "sold_quantity"
        )
    )

    # ========================================================
    # AVALIAÇÃO
    # ========================================================

    rating_star = None

    seller_reputation = (
        produto_api.get(
            "seller_reputation"
        )
        or {}
    )

    if isinstance(
        seller_reputation,
        dict,
    ):

        rating_star = seller_reputation.get(
            "seller_reputation_level"
        )

    # ========================================================
    # PRODUTO NORMALIZADO
    # ========================================================
    #
    # Mantemos nomes semelhantes aos usados
    # anteriormente pela Shopee.
    #
    # Isso permite que o restante do sistema
    # continue usando os mesmos campos.
    #
    # ========================================================

    produto = {

        # ----------------------------------------------------
        # IDENTIFICAÇÃO
        # ----------------------------------------------------

        "itemId": item_id_retorno,

        "shopId": (
            shop_id
            or seller_id
        ),

        "sellerId": seller_id,

        # ----------------------------------------------------
        # PRODUTO
        # ----------------------------------------------------

        "productName": (
            produto_api.get(
                "title"
            )
            or "Produto"
        ),

        "title": (
            produto_api.get(
                "title"
            )
            or "Produto"
        ),

        # ----------------------------------------------------
        # PREÇOS
        # ----------------------------------------------------

        "price": price,

        "priceMin": price,

        "priceMax": price,

        "originalPrice": original_price,

        "priceDiscountRate": (
            price_discount_rate
        ),

        "currencyId": (
            produto_api.get(
                "currency_id"
            )
        ),

        # ----------------------------------------------------
        # VENDAS
        # ----------------------------------------------------

        "sales": sales,

        "soldQuantity": sales,

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        "imageUrl": image_url,

        "thumbnail": (
            produto_api.get(
                "thumbnail"
            )
        ),

        # ----------------------------------------------------
        # LINKS
        # ----------------------------------------------------

        "productLink": product_link,

        "permalink": product_link,

        # ----------------------------------------------------
        # LOJA / VENDEDOR
        # ----------------------------------------------------

        "shopName": None,

        "sellerId": seller_id,

        "shopType": None,

        # ----------------------------------------------------
        # AVALIAÇÃO
        # ----------------------------------------------------

        "ratingStar": rating_star,

        # ----------------------------------------------------
        # OUTROS
        # ----------------------------------------------------

        "categoryId": (
            produto_api.get(
                "category_id"
            )
        ),

        "condition": (
            produto_api.get(
                "condition"
            )
        ),

        "availableQuantity": (
            produto_api.get(
                "available_quantity"
            )
        ),

        "listingTypeId": (
            produto_api.get(
                "listing_type_id"
            )
        ),

        "buyingMode": (
            produto_api.get(
                "buying_mode"
            )
        ),

        "siteId": (
            produto_api.get(
                "site_id"
            )
        ),

        # ----------------------------------------------------
        # RESPOSTA ORIGINAL
        # ----------------------------------------------------

        "mercadolivreData": produto_api,
    }

    # ========================================================
    # LOG
    # ========================================================

    logger.info(
        "Produto encontrado: %s",
        produto.get(
            "productName"
        ),
    )

    logger.info(
        "Item ID retornado: %s",
        produto.get(
            "itemId"
        ),
    )

    logger.info(
        "Seller ID retornado: %s",
        produto.get(
            "sellerId"
        ),
    )

    logger.info(
        "Preço retornado: %s",
        produto.get(
            "price"
        ),
    )

    return produto


# ============================================================
# BUSCAR PRODUTO POR LINK
# ============================================================

def buscar_produto_por_link(
    link: str
):

    if not link:

        raise MercadoLivreAPIError(
            "Link vazio."
        )

    link = link.strip()

    # ========================================================
    # RESOLVER LINK
    # ========================================================

    url_final = resolver_link(
        link
    )

    # ========================================================
    # EXTRAIR ITEM ID
    # ========================================================

    item_id = extrair_item_id_da_url(
        url_final
    )

    logger.info(
        "Item ID extraído com sucesso: %s",
        item_id,
    )

    # ========================================================
    # CONSULTAR PRODUTO
    # ========================================================

    produto = buscar_produto_por_ids(
        item_id=item_id
    )

    # ========================================================
    # NÃO ENCONTROU
    # ========================================================

    if not produto:

        raise MercadoLivreAPIError(
            "O produto não foi encontrado "
            "na API do Mercado Livre."
        )

    # ========================================================
    # PRESERVAR LINK ORIGINAL
    # ========================================================
    #
    # IMPORTANTE:
    #
    # Se o usuário passou:
    #
    # https://meli.la/12w2nSd
    #
    # esse é o link que deverá ser usado
    # como link de afiliado.
    #
    # Não substituímos pelo permalink normal
    # do Mercado Livre.
    #
    # ========================================================

    produto[
        "manualAffiliateLink"
    ] = link

    produto[
        "affiliateLink"
    ] = link

    # ========================================================
    # CAMPOS EXTRAS
    # ========================================================

    produto[
        "originalAffiliateLink"
    ] = link

    produto[
        "resolvedProductLink"
    ] = url_final

    produto[
        "itemId"
    ] = item_id

    logger.info(
        "Link de afiliado original "
        "preservado: %s",
        link,
    )

    logger.info(
        "URL final do produto: %s",
        url_final,
    )

    return produto


# ============================================================
# TESTE RÁPIDO
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s "
            "%(message)s"
        ),
    )

    link_teste = os.getenv(
        "MERCADOLIVRE_LINK_TESTE",
        "",
    ).strip()

    if not link_teste:

        print(
            "Defina a variável "
            "MERCADOLIVRE_LINK_TESTE "
            "para executar o teste."
        )

    else:

        try:

            produto = buscar_produto_por_link(
                link_teste
            )

            print()
            print(
                "Produto encontrado:"
            )
            print(
                "Nome:",
                produto.get(
                    "productName"
                ),
            )
            print(
                "Preço:",
                produto.get(
                    "price"
                ),
            )
            print(
                "Imagem:",
                produto.get(
                    "imageUrl"
                ),
            )
            print(
                "Item ID:",
                produto.get(
                    "itemId"
                ),
            )
            print(
                "Link afiliado:",
                produto.get(
                    "affiliateLink"
                ),
            )

        except MercadoLivreAPIError as erro:

            logger.error(
                "Erro: %s",
                erro,
            )
