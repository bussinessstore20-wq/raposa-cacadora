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
# SESSÃO
# ============================================================

def _obter_sessao() -> requests.Session:
    session = requests.Session()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
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

    session.headers.update(headers)

    return session


# ============================================================
# ITEM ID
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:

    if not conteudo:
        return None

    texto = str(conteudo)

    match = re.search(
        r"MLB[-_]?(\d{8,10})",
        texto,
        re.IGNORECASE,
    )

    if match:

        return (
            f"MLB{match.group(1)}"
            .upper()
        )

    try:

        parsed = urlparse(texto)

        if parsed.query:

            params = parse_qs(
                parsed.query
            )

            for key in (
                "item_id",
                "itemId",
                "itemid",
            ):

                valores = params.get(key)

                if not valores:
                    continue

                for valor in valores:

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
            "Valor vazio para extração do Item ID."
        )

    item_id = extrair_item_id_da_string(
        str(valor).strip()
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

    link = (
        str(link or "")
        .strip()
    )

    if not link:

        raise MercadoLivreAPIError(
            "Link do Mercado Livre está vazio."
        )

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
            "Erro ao resolver link do Mercado Livre: "
            f"{erro}"
        ) from erro

    logger.info(
        "HTTP ao resolver link: %d",
        response.status_code,
    )

    url_final = (
        response.url
        or link
    )

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    # ========================================================
    # TENTA URL FINAL
    # ========================================================

    item_id = extrair_item_id_da_string(
        url_final
    )

    if item_id:

        logger.info(
            "Item ID extraído da URL final: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # ========================================================
    # TENTA URL ORIGINAL
    # ========================================================

    item_id = extrair_item_id_da_string(
        link
    )

    if item_id:

        logger.info(
            "Item ID extraído do link original: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # ========================================================
    # VITRINE / SOCIAL
    # ========================================================

    if (
        response.status_code < 400
        and response.text
    ):

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        for tag in soup.find_all(
            "a",
            href=True,
        ):

            href = str(
                tag.get("href")
                or ""
            )

            item_id = (
                extrair_item_id_da_string(
                    href
                )
            )

            if item_id:

                logger.info(
                    "Produto encontrado "
                    "na vitrine: %s",
                    item_id,
                )

                return (
                    url_final,
                    item_id,
                )

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
                "Produto encontrado "
                "via HTML: %s",
                item_id,
            )

            return (
                url_final,
                item_id,
            )

    raise MercadoLivreAPIError(
        "Não foi possível extrair o Item ID "
        "do link do Mercado Livre."
    )


# ============================================================
# GET API
# ============================================================

def _api_get(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
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

        raise MercadoLivreAPIError(
            f"Mercado Livre respondeu HTTP "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

    try:

        dados = response.json()

    except ValueError as erro:

        raise MercadoLivreAPIError(
            "Resposta da API do Mercado Livre "
            "não é JSON válido."
        ) from erro

    if not isinstance(
        dados,
        dict,
    ):

        raise MercadoLivreAPIError(
            "Resposta da API não é um objeto JSON."
        )

    return dados


# ============================================================
# API DE PRODUTO
# ============================================================

def _consultar_item_api(
    item_id: str,
) -> Dict[str, Any]:

    return _api_get(
        f"/items/{item_id}"
    )


# ============================================================
# FALLBACK WEB
# ============================================================

def _consultar_item_via_web(
    item_id: str,
) -> Dict[str, Any]:

    url_produto = (
        "https://produto.mercadolivre.com.br/"
        f"{item_id}"
    )

    logger.info(
        "Iniciando fallback Web: %s",
        url_produto,
    )

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,"
                "image/webp,*/*;q=0.8"
            ),
            "Accept-Language": (
                "pt-BR,pt;q=0.9,en-US;q=0.8"
            ),
        }
    )

    try:

        response = session.get(
            url_produto,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            f"Erro no fallback Web: {erro}"
        ) from erro

    logger.info(
        "Fallback Web HTTP %d | URL final: %s",
        response.status_code,
        response.url,
    )

    if response.status_code != 200:

        raise MercadoLivreAPIError(
            "Mercado Livre bloqueou o fallback Web "
            f"com HTTP {response.status_code}."
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    # ========================================================
    # TÍTULO
    # ========================================================

    titulo = None

    titulo_elem = soup.find(
        "h1",
        class_="ui-pdp-title",
    )

    if titulo_elem:

        titulo = (
            titulo_elem.get_text(
                strip=True
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
                meta_title.get("content")
                or None
            )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    meta_image = soup.find(
        "meta",
        attrs={
            "property": "og:image"
        },
    )

    if meta_image:

        image_url = (
            meta_image.get("content")
            or None
        )

    if not image_url:

        img = soup.find(
            "img",
            class_="ui-pdp-image",
        )

        if img:

            image_url = (
                img.get("src")
                or img.get("data-zoom")
            )

    # ========================================================
    # PREÇO
    # ========================================================

    price = None

    meta_price = soup.find(
        "meta",
        attrs={
            "itemprop": "price"
        },
    )

    if meta_price:

        try:

            price = float(
                meta_price.get(
                    "content"
                )
            )

        except Exception:
            pass

    # ========================================================
    # JSON-LD
    # ========================================================

    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:

        if not script.string:
            continue

        try:

            dados = json.loads(
                script.string
            )

        except Exception:
            continue

        candidatos = (
            dados
            if isinstance(
                dados,
                list,
            )
            else [dados]
        )

        for candidato in candidatos:

            if not isinstance(
                candidato,
                dict,
            ):
                continue

            if not titulo:

                titulo = (
                    candidato.get(
                        "name"
                    )
                    or titulo
                )

            if not image_url:

                imagem = candidato.get(
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

                if isinstance(
                    imagem,
                    str,
                ):

                    image_url = imagem

            offers = candidato.get(
                "offers"
            )

            if isinstance(
                offers,
                list,
            ):

                offers = (
                    offers[0]
                    if offers
                    else {}
                )

            if isinstance(
                offers,
                dict,
            ):

                valor = offers.get(
                    "price"
                )

                if (
                    price is None
                    and valor is not None
                ):

                    try:

                        price = float(
                            valor
                        )

                    except Exception:
                        pass

    if not titulo:

        titulo = "Produto Mercado Livre"

    return {
        "id": item_id,
        "title": titulo,
        "price": price,
        "pictures": (
            [
                {
                    "secure_url": image_url
                }
            ]
            if image_url
            else []
        ),
        "permalink": url_produto,
        "condition": "new",
    }


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

    # ========================================================
    # PRIMEIRA TENTATIVA: API
    # ========================================================

    try:

        return _consultar_item_api(
            item_id
        )

    except MercadoLivreAPIError as erro:

        mensagem = str(
            erro
        )

        logger.warning(
            "API do Mercado Livre falhou "
            "para %s: %s",
            item_id,
            mensagem,
        )

        # ====================================================
        # 403 -> FALLBACK WEB
        # ====================================================

        if "403" in mensagem:

            logger.warning(
                "API bloqueada com HTTP 403. "
                "Tentando fallback Web para %s.",
                item_id,
            )

            try:

                return _consultar_item_via_web(
                    item_id
                )

            except MercadoLivreAPIError as erro_web:

                logger.error(
                    "Fallback Web também falhou "
                    "para %s: %s",
                    item_id,
                    erro_web,
                )

                raise MercadoLivreAPIError(
                    "Não foi possível obter os dados "
                    f"do produto {item_id}. "
                    "A API retornou HTTP 403 e o "
                    "fallback Web também foi bloqueado."
                ) from erro_web

        raise


# ============================================================
# FORMATAR PRODUTO
# ============================================================

def buscar_produto_por_ids(
    item_id: Any,
    shop_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:

    logger.info(
        "Consultando produto: itemId=%s",
        item_id,
    )

    item_id_normalizado = extrair_item_id(
        str(item_id)
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

    item_id_retorno = (
        produto_api.get("id")
        or item_id_normalizado
    )

    seller_id = produto_api.get(
        "seller_id"
    )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    pictures = (
        produto_api.get("pictures")
        or []
    )

    for picture in pictures:

        if not isinstance(
            picture,
            dict,
        ):
            continue

        image_url = (
            picture.get("secure_url")
            or picture.get("url")
        )

        if image_url:
            break

    if not image_url:

        image_url = (
            produto_api.get(
                "thumbnail"
            )
        )

    # ========================================================
    # PREÇOS
    # ========================================================

    price = produto_api.get(
        "price"
    )

    original_price = produto_api.get(
        "original_price"
    )

    discount = None

    try:

        if (
            original_price
            and price
            and float(original_price) > 0
            and float(price)
            < float(original_price)
        ):

            discount = round(
                (
                    (
                        float(original_price)
                        - float(price)
                    )
                    / float(original_price)
                )
                * 100,
                2,
            )

    except Exception:
        discount = None

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

        "productName": (
            produto_api.get(
                "title"
            )
            or "Produto Mercado Livre"
        ),

        "title": (
            produto_api.get(
                "title"
            )
            or "Produto Mercado Livre"
        ),

        "price": price,

        "priceMin": price,

        "priceMax": price,

        "originalPrice": original_price,

        "priceDiscountRate": discount,

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
    # CONSULTAR
    # ========================================================

    produto = buscar_produto_por_ids(
        item_id=item_id
    )

    if not produto:

        raise MercadoLivreAPIError(
            "Produto não encontrado."
        )

    # ========================================================
    # PRESERVAR LINKS
    # ========================================================

    produto.update(
        {
            "manualAffiliateLink": link,

            "affiliateLink": link,

            "originalAffiliateLink": link,

            "resolvedProductLink": url_final,

            "itemId": item_id,
        }
    )

    logger.info(
        "Produto encontrado: %s",
        produto.get(
            "productName",
            "Produto",
        ),
    )

    return produto
