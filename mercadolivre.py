import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from mercadolivre_oauth import (
    obter_access_token_mercadolivre,
)

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

MERCADOLIVRE_API_URL = os.getenv(
    "MERCADOLIVRE_API_URL",
    "https://api.mercadolibre.com",
).strip()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 30


# ============================================================
# ERRO
# ============================================================

class MercadoLivreAPIError(Exception):
    """Erro relacionado ao Mercado Livre."""


# ============================================================
# SESSÃO HTTP
# ============================================================

def _obter_sessao() -> requests.Session:
    """
    Cria uma sessão HTTP.

    O access token é obtido pelo módulo OAuth.
    """

    session = requests.Session()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": (
            "application/json, "
            "text/plain, "
            "*/*"
        ),
        "Accept-Language": (
            "pt-BR,pt;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Accept-Encoding": (
            "gzip, deflate"
        ),
        "Sec-Ch-Ua": (
            '"Chromium";v="128", '
            '"Not;A=Brand";v="24", '
            '"Google Chrome";v="128"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }

    try:

        access_token = (
            obter_access_token_mercadolivre()
        )

    except Exception as erro:

        logger.warning(
            "Não foi possível obter access token "
            "do Mercado Livre: %s",
            erro,
        )

        access_token = ""

    if access_token:

        headers["Authorization"] = (
            f"Bearer {access_token}"
        )

    session.headers.update(
        headers
    )

    return session


# ============================================================
# EXTRAÇÃO DE ITEM ID
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:
    """
    Procura MLBxxxxxxxxxx dentro de uma string.
    """

    if not conteudo:
        return None

    match = re.search(
        r"MLB[-_]?(\d{8,10})",
        conteudo,
        re.IGNORECASE,
    )

    if match:

        return (
            f"MLB{match.group(1)}"
            .upper()
        )

    try:

        parsed = urlparse(
            conteudo
        )

        if parsed.query:

            params = parse_qs(
                parsed.query
            )

            for key in (
                "item_id",
                "itemId",
                "itemid",
            ):

                if key not in params:
                    continue

                valor = params[key][0]

                match = re.search(
                    r"MLB[-_]?(\d{8,10})",
                    valor,
                    re.IGNORECASE,
                )

                if match:

                    return (
                        f"MLB{match.group(1)}"
                        .upper()
                    )

    except Exception:

        pass

    return None


def extrair_item_id(
    valor: str,
) -> str:

    if not valor:

        raise MercadoLivreAPIError(
            "Valor vazio para extração "
            "do Item ID."
        )

    item_id = (
        extrair_item_id_da_string(
            str(valor).strip()
        )
    )

    if not item_id:

        raise MercadoLivreAPIError(
            "Item ID inválido ou não encontrado: "
            f"{valor}"
        )

    return item_id


# ============================================================
# RESOLVER LINK
# ============================================================

def resolver_link_e_extrair_id(
    link: str,
) -> Tuple[str, str]:
    """
    Resolve:

    - meli.la
    - links normais
    - vitrines /social/

    Retorna:

        url_final
        item_id
    """

    if not link:

        raise MercadoLivreAPIError(
            "Link do Mercado Livre está vazio."
        )

    link = link.strip()

    logger.info(
        "Resolvendo link do Mercado Livre: %s",
        link,
    )

    session = _obter_sessao()

    try:

        response = session.get(
            link,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao resolver link "
            f"do Mercado Livre: {erro}"
        ) from erro

    logger.info(
        "HTTP ao resolver link: %d",
        response.status_code,
    )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Mercado Livre retornou HTTP "
            f"{response.status_code} ao resolver "
            "o link."
        )

    url_final = (
        response.url
        or link
    )

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    # --------------------------------------------------------
    # TENTA PEGAR MLB NA URL
    # --------------------------------------------------------

    item_id = (
        extrair_item_id_da_string(
            url_final
        )
    )

    if item_id:

        logger.info(
            "Item ID extraído diretamente "
            "da URL final: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # --------------------------------------------------------
    # VITRINE
    # --------------------------------------------------------

    if (
        "/social/" in url_final.lower()
        and response.text
    ):

        logger.info(
            "Página de vitrine detectada. "
            "Procurando produto..."
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # LINKS
        # ----------------------------------------------------

        for tag_a in soup.find_all(
            "a",
            href=True,
        ):

            href = (
                tag_a.get("href")
                or ""
            )

            item_id = (
                extrair_item_id_da_string(
                    href
                )
            )

            if item_id:

                logger.info(
                    "Produto encontrado na vitrine: %s",
                    item_id,
                )

                return (
                    url_final,
                    item_id,
                )

        # ----------------------------------------------------
        # HTML COMPLETO
        # ----------------------------------------------------

        matches = re.findall(
            r"MLB[-_]?(\d{8,10})",
            response.text,
            re.IGNORECASE,
        )

        if matches:

            item_id = (
                f"MLB{matches[0]}"
                .upper()
            )

            logger.info(
                "Produto encontrado via HTML: %s",
                item_id,
            )

            return (
                url_final,
                item_id,
            )

    raise MercadoLivreAPIError(
        "Não foi possível extrair nenhum "
        "produto válido do link."
    )


# ============================================================
# API GET
# ============================================================

def _api_get(
    endpoint: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Dict[str, Any]:

    url = (
        f"{MERCADOLIVRE_API_URL.rstrip('/')}/"
        f"{endpoint.lstrip('/')}"
    )

    session = _obter_sessao()

    logger.info(
        "Consultando API do Mercado Livre: %s",
        url,
    )

    try:

        response = session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro de conexão com a API "
            f"do Mercado Livre: {erro}"
        ) from erro

    logger.info(
        "Mercado Livre HTTP %d",
        response.status_code,
    )

    if response.status_code != 200:

        corpo = (
            response.text[:500]
            if response.text
            else ""
        )

        logger.error(
            "Mercado Livre retornou HTTP %d.",
            response.status_code,
        )

        raise MercadoLivreAPIError(
            "Mercado Livre respondeu HTTP "
            f"{response.status_code}: "
            f"{corpo}"
        )

    try:

        dados = response.json()

    except ValueError as erro:

        raise MercadoLivreAPIError(
            "A API do Mercado Livre retornou "
            "resposta inválida."
        ) from erro

    if not isinstance(
        dados,
        dict,
    ):

        raise MercadoLivreAPIError(
            "A API retornou dados em formato "
            "inesperado."
        )

    return dados


# ============================================================
# EXTRAIR VALOR FLOAT
# ============================================================

def _float_or_none(
    valor: Any,
) -> Optional[float]:

    if valor is None:
        return None

    try:

        return float(valor)

    except (
        ValueError,
        TypeError,
    ):

        return None


# ============================================================
# EXTRAIR JSON-LD
# ============================================================

def _extrair_json_ld(
    soup: BeautifulSoup,
) -> list[Dict[str, Any]]:

    resultado = []

    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:

        conteudo = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not conteudo:
            continue

        try:

            dados = json.loads(
                conteudo
            )

        except Exception:

            continue

        if isinstance(
            dados,
            dict,
        ):

            resultado.append(
                dados
            )

        elif isinstance(
            dados,
            list,
        ):

            resultado.extend(
                item
                for item in dados
                if isinstance(
                    item,
                    dict,
                )
            )

    return resultado


# ============================================================
# DETECTAR BLOQUEIO WEB
# ============================================================

def _pagina_web_bloqueada(
    response: requests.Response,
) -> bool:
    """
    Detecta account-verification, captcha
    e páginas de segurança.
    """

    url = (
        str(
            response.url
            or ""
        )
        .lower()
    )

    html = (
        response.text
        or ""
    ).lower()

    indicadores_url = (
        "account-verification",
        "/captcha",
        "captcha",
        "challenge",
        "security-check",
        "verify",
    )

    indicadores_html = (
        "verificação de segurança",
        "verificacao de seguranca",
        "security verification",
        "account verification",
        "captcha",
        "verify that you are human",
        "verifique que você é humano",
        "verifique que voce e humano",
    )

    if any(
        indicador in url
        for indicador in indicadores_url
    ):

        return True

    if any(
        indicador in html
        for indicador in indicadores_html
    ):

        return True

    return False


# ============================================================
# FALLBACK WEB
# ============================================================

def _consultar_item_via_web(
    item_id: str,
) -> Dict[str, Any]:
    """
    Tenta obter dados reais do anúncio via Web.

    IMPORTANTE:

    HTTP 200 não é suficiente.

    Se o Mercado Livre devolver
    account-verification, o método falha
    em vez de criar um produto falso.
    """

    item_id = (
        str(item_id)
        .strip()
        .upper()
    )

    url_produto = (
        "https://produto.mercadolivre.com.br/"
        f"{item_id}"
    )

    logger.info(
        "Iniciando fallback Web: %s",
        url_produto,
    )

    session = _obter_sessao()

    session.headers.update(
        {
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,"
                "image/webp,"
                "*/*;q=0.8"
            ),
            "Accept-Language": (
                "pt-BR,pt;q=0.9,"
                "en-US;q=0.8,en;q=0.7"
            ),
            "Upgrade-Insecure-Requests": "1",
        }
    )

    try:

        response = session.get(
            url_produto,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao acessar página Web "
            f"do produto: {erro}"
        ) from erro

    logger.info(
        "Fallback Web HTTP %d | URL final: %s",
        response.status_code,
        response.url,
    )

    if response.status_code != 200:

        raise MercadoLivreAPIError(
            "Erro ao acessar página Web "
            f"do produto: HTTP "
            f"{response.status_code}"
        )

    # --------------------------------------------------------
    # BLOQUEIO
    # --------------------------------------------------------

    if _pagina_web_bloqueada(
        response
    ):

        logger.warning(
            "Mercado Livre redirecionou o fallback "
            "para página de verificação/bloqueio."
        )

        raise MercadoLivreAPIError(
            "Mercado Livre bloqueou o acesso Web "
            "ao anúncio com página de verificação."
        )

    html = (
        response.text
        or ""
    )

    if not html.strip():

        raise MercadoLivreAPIError(
            "Mercado Livre retornou página Web vazia."
        )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    titulo = None
    preco = None
    original_price = None
    image_url = None
    description = None
    currency = "BRL"

    product_url = (
        str(
            response.url
            or url_produto
        )
    )

    # ========================================================
    # JSON-LD
    # ========================================================

    json_ld = _extrair_json_ld(
        soup
    )

    for dados in json_ld:

        nome = dados.get(
            "name"
        )

        if (
            not titulo
            and isinstance(
                nome,
                str,
            )
            and nome.strip()
        ):

            titulo = nome.strip()

        descricao = dados.get(
            "description"
        )

        if (
            not description
            and isinstance(
                descricao,
                str,
            )
            and descricao.strip()
        ):

            description = (
                descricao.strip()
            )

        imagem = dados.get(
            "image"
        )

        if isinstance(
            imagem,
            list,
        ):

            imagem = (
                imagem[0]
                if imagem
                else None
            )

        if (
            not image_url
            and isinstance(
                imagem,
                str,
            )
            and imagem.strip()
        ):

            image_url = imagem.strip()

        offers = dados.get(
            "offers"
        )

        if isinstance(
            offers,
            list,
        ):

            offers = (
                offers[0]
                if offers
                else None
            )

        if isinstance(
            offers,
            dict,
        ):

            if preco is None:

                preco = _float_or_none(
                    offers.get(
                        "price"
                    )
                )

            moeda = offers.get(
                "priceCurrency"
            )

            if moeda:

                currency = (
                    str(moeda)
                    .upper()
                )

    # ========================================================
    # H1 MERCADO LIVRE
    # ========================================================

    if not titulo:

        titulo_elem = soup.find(
            "h1",
            class_="ui-pdp-title",
        )

        if titulo_elem:

            titulo = (
                titulo_elem.get_text(
                    " ",
                    strip=True,
                )
            )

    # ========================================================
    # H1 GENÉRICO
    # ========================================================

    if not titulo:

        titulo_elem = soup.find(
            "h1"
        )

        if titulo_elem:

            titulo = (
                titulo_elem.get_text(
                    " ",
                    strip=True,
                )
            )

    # ========================================================
    # META TITLE
    # ========================================================

    if not titulo:

        meta_title = soup.find(
            "meta",
            attrs={
                "property": "og:title"
            },
        )

        if meta_title:

            titulo = (
                meta_title.get(
                    "content"
                )
                or None
            )

    # ========================================================
    # IMAGEM OG
    # ========================================================

    if not image_url:

        meta_image = soup.find(
            "meta",
            attrs={
                "property": "og:image"
            },
        )

        if meta_image:

            image_url = (
                meta_image.get(
                    "content"
                )
                or None
            )

    # ========================================================
    # PREÇO META
    # ========================================================

    if preco is None:

        meta_price = soup.find(
            "meta",
            attrs={
                "itemprop": "price"
            },
        )

        if meta_price:

            preco = _float_or_none(
                meta_price.get(
                    "content"
                )
            )

    # ========================================================
    # CURRENCY META
    # ========================================================

    meta_currency = soup.find(
        "meta",
        attrs={
            "itemprop": "priceCurrency"
        },
    )

    if meta_currency:

        currency = (
            meta_currency.get(
                "content"
            )
            or currency
        )

    # ========================================================
    # DESCRIÇÃO
    # ========================================================

    if not description:

        meta_description = soup.find(
            "meta",
            attrs={
                "name": "description"
            },
        )

        if meta_description:

            description = (
                meta_description.get(
                    "content"
                )
                or None
            )

    # ========================================================
    # URL CANÔNICA
    # ========================================================

    canonical = soup.find(
        "link",
        rel="canonical",
    )

    if canonical:

        href = (
            canonical.get(
                "href"
            )
            or ""
        ).strip()

        if href:

            product_url = href

    # ========================================================
    # VALIDAR PRODUTO
    # ========================================================

    if not titulo:

        logger.error(
            "Fallback Web não conseguiu "
            "extrair título real do produto %s.",
            item_id,
        )

        raise MercadoLivreAPIError(
            "Mercado Livre não disponibilizou "
            "os dados do anúncio via Web."
        )

    titulo_limpo = (
        titulo.strip()
    )

    titulo_lower = (
        titulo_limpo.lower()
    )

    titulos_invalidos = {
        "produto mercado livre",
        "mercado livre",
        "mercadolivre",
        "entrar",
        "login",
        "verificação",
        "verificacao",
    }

    if titulo_lower in titulos_invalidos:

        raise MercadoLivreAPIError(
            "Página retornada não contém "
            "dados reais do produto."
        )

    # ========================================================
    # PRODUTO
    # ========================================================

    produto = {
        "id": item_id,

        "title": titulo_limpo,

        "price": preco,

        "original_price": original_price,

        "currency_id": currency,

        "pictures": (
            [
                {
                    "secure_url": image_url
                }
            ]
            if image_url
            else []
        ),

        "permalink": product_url,

        "condition": "new",

        "description": description,
    }

    logger.info(
        "Fallback Web encontrou produto real: %s",
        titulo_limpo,
    )

    return produto


# ============================================================
# CONSULTAR ITEM
# ============================================================

def _consultar_item(
    item_id: str,
) -> Dict[str, Any]:

    item_id = (
        str(item_id)
        .strip()
        .upper()
    )

    if not re.fullmatch(
        r"MLB\d{8,10}",
        item_id,
    ):

        raise MercadoLivreAPIError(
            "Formato de Item ID inválido: "
            f"{item_id}"
        )

    try:

        return _api_get(
            f"/items/{item_id}"
        )

    except MercadoLivreAPIError as erro:

        logger.warning(
            "API do Mercado Livre falhou "
            "para %s: %s",
            item_id,
            erro,
        )

        if "403" not in str(erro):

            raise

        logger.warning(
            "API bloqueada com HTTP 403. "
            "Tentando fallback Web para %s.",
            item_id,
        )

        return _consultar_item_via_web(
            item_id
        )


# ============================================================
# BUSCAR PRODUTO POR ID
# ============================================================

def buscar_produto_por_ids(
    item_id: Any,
    shop_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:

    logger.info(
        "Consultando produto: itemId=%s",
        item_id,
    )

    item_id_normalizado = (
        extrair_item_id(
            str(item_id)
        )
    )

    produto_api = _consultar_item(
        item_id_normalizado
    )

    if not produto_api:

        logger.warning(
            "Nenhum dado retornado para %s",
            item_id_normalizado,
        )

        return None

    # ========================================================
    # ID
    # ========================================================

    item_id_retorno = (
        produto_api.get("id")
        or item_id_normalizado
    )

    # ========================================================
    # SELLER
    # ========================================================

    seller_id = produto_api.get(
        "seller_id"
    )

    # ========================================================
    # TÍTULO
    # ========================================================

    product_name = (
        produto_api.get(
            "title"
        )
        or produto_api.get(
            "name"
        )
    )

    if not product_name:

        raise MercadoLivreAPIError(
            "Mercado Livre não retornou "
            "o nome do produto."
        )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    pictures = (
        produto_api.get(
            "pictures"
        )
        or []
    )

    if isinstance(
        pictures,
        list,
    ):

        for picture in pictures:

            if not isinstance(
                picture,
                dict,
            ):
                continue

            image_url = (
                picture.get(
                    "secure_url"
                )
                or picture.get(
                    "url"
                )
            )

            if image_url:
                break

    # ========================================================
    # PREÇO
    # ========================================================

    price = _float_or_none(
        produto_api.get(
            "price"
        )
    )

    original_price = _float_or_none(
        produto_api.get(
            "original_price"
        )
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

    sales = produto_api.get(
        "sold_quantity"
    )

    # ========================================================
    # LINK
    # ========================================================

    product_link = (
        produto_api.get(
            "permalink"
        )
    )

    # ========================================================
    # REPUTAÇÃO
    # ========================================================

    seller_reputation = (
        produto_api.get(
            "seller_reputation"
        )
        or {}
    )

    rating_star = None

    if isinstance(
        seller_reputation,
        dict,
    ):

        rating_star = (
            seller_reputation.get(
                "seller_reputation_level"
            )
        )

    # ========================================================
    # PRODUTO FINAL
    # ========================================================

    produto = {
        "itemId": item_id_retorno,

        "shopId": (
            shop_id
            or seller_id
        ),

        "sellerId": seller_id,

        "productName": product_name,

        "title": product_name,

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
            or "BRL"
        ),

        "sales": sales,

        "soldQuantity": sales,

        "imageUrl": image_url,

        "thumbnail": (
            produto_api.get(
                "thumbnail"
            )
        ),

        "productLink": product_link,

        "permalink": product_link,

        "shopName": None,

        "shopType": None,

        "ratingStar": rating_star,

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

        "description": (
            produto_api.get(
                "description"
            )
        ),

        "mercadolivreData": produto_api,
    }

    logger.info(
        "Produto processado com sucesso: %s",
        produto.get(
            "productName"
        ),
    )

    return produto


# ============================================================
# BUSCAR PRODUTO POR LINK
# ============================================================

def buscar_produto_por_link(
    link: str,
) -> Dict[str, Any]:
    """
    Fluxo completo:

    1. Resolve meli.la.
    2. Resolve vitrine.
    3. Extrai MLB.
    4. Consulta API.
    5. Se API 403, tenta Web.
    6. Valida se Web realmente entregou produto.
    7. Preserva link de afiliado.
    """

    if not link:

        raise MercadoLivreAPIError(
            "Link de entrada está vazio."
        )

    link = link.strip()

    # ========================================================
    # RESOLVER
    # ========================================================

    url_final, item_id = (
        resolver_link_e_extrair_id(
            link
        )
    )

    logger.info(
        "Produto identificado: %s",
        item_id,
    )

    # ========================================================
    # BUSCAR
    # ========================================================

    produto = buscar_produto_por_ids(
        item_id=item_id
    )

    if not produto:

        raise MercadoLivreAPIError(
            "O produto não foi encontrado "
            "no Mercado Livre."
        )

    # ========================================================
    # VALIDAR NOME
    # ========================================================

    nome = (
        produto.get(
            "productName"
        )
        or ""
    ).strip()

    if not nome:

        raise MercadoLivreAPIError(
            "Produto retornado sem nome."
        )

    # ========================================================
    # PRESERVAR LINK
    # ========================================================

    produto.update(
        {
            "manualAffiliateLink": link,

            "affiliateLink": link,

            "originalAffiliateLink": link,

            "resolvedProductLink": (
                url_final
            ),

            "itemId": item_id,
        }
    )

    return produto
