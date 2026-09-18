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
    pass


# ============================================================
# SESSÃO API
# ============================================================

def _obter_sessao_api() -> requests.Session:
    session = requests.Session()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
    }

    try:
        access_token = (
            obter_access_token_mercadolivre()
        )

    except Exception as erro:
        logger.warning(
            "Não foi possível obter access token do Mercado Livre: %s",
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
# SESSÃO WEB
# ============================================================

def _obter_sessao_web() -> requests.Session:
    session = requests.Session()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "*/*;q=0.8"
        ),
        "Accept-Language": (
            "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }

    session.headers.update(headers)

    return session


# ============================================================
# EXTRAÇÃO DO ITEM ID
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:

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
        parsed = urlparse(conteudo)

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

                valor = valores[0]

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
            f"Item ID inválido ou não encontrado: {valor}"
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

    session = _obter_sessao_web()

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

    url_final = response.url or ""

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    # --------------------------------------------------------
    # TENTA ITEM ID NA URL
    # --------------------------------------------------------

    item_id = extrair_item_id_da_string(
        url_final
    )

    if item_id:

        logger.info(
            "Item ID encontrado na URL final: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # --------------------------------------------------------
    # ITEM ID NO HTML
    # --------------------------------------------------------

    html = response.text or ""

    item_id = extrair_item_id_da_string(
        html
    )

    if item_id:

        logger.info(
            "Produto encontrado no HTML: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # --------------------------------------------------------
    # VITRINE / SOCIAL
    # --------------------------------------------------------

    if (
        "/social/" in url_final
        or "/lists" in url_final
    ):

        logger.info(
            "Página de vitrine detectada. "
            "Procurando produto..."
        )

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # ----------------------------------------------------
        # LINKS
        # ----------------------------------------------------

        for tag in soup.find_all(
            "a",
            href=True,
        ):

            href = str(
                tag.get("href") or ""
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
        # REGEX
        # ----------------------------------------------------

        matches = re.findall(
            r"MLB[-_]?(\d{8,10})",
            html,
            re.IGNORECASE,
        )

        if matches:

            item_id = (
                f"MLB{matches[0]}"
                .upper()
            )

            logger.info(
                "Produto encontrado via regex: %s",
                item_id,
            )

            return (
                url_final,
                item_id,
            )

    raise MercadoLivreAPIError(
        "Não foi possível extrair um produto "
        "do link informado."
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

    session = _obter_sessao_api()

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
            f"Mercado Livre respondeu HTTP "
            f"{response.status_code}: {corpo}"
        )

    try:

        dados = response.json()

    except ValueError as erro:

        raise MercadoLivreAPIError(
            "A API do Mercado Livre retornou "
            "JSON inválido."
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
# FALLBACK WEB
# ============================================================

def _extrair_json_ld(
    soup: BeautifulSoup,
) -> Dict[str, Any]:

    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:

        texto = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not texto:
            continue

        try:

            dados = json.loads(
                texto
            )

        except Exception:
            continue

        candidatos = []

        if isinstance(
            dados,
            list,
        ):
            candidatos.extend(
                dados
            )

        elif isinstance(
            dados,
            dict,
        ):

            candidatos.append(
                dados
            )

            graph = dados.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):
                candidatos.extend(
                    graph
                )

        for item in candidatos:

            if not isinstance(
                item,
                dict,
            ):
                continue

            tipo = item.get(
                "@type"
            )

            if (
                tipo == "Product"
                or (
                    isinstance(
                        tipo,
                        list,
                    )
                    and "Product" in tipo
                )
            ):

                return item

    return {}


def _consultar_item_via_web(
    item_id: str,
) -> Dict[str, Any]:

    urls = [
        (
            "https://produto.mercadolivre.com.br/"
            f"{item_id}"
        ),
        (
            "https://www.mercadolivre.com.br/"
            f"{item_id}"
        ),
    ]

    session = _obter_sessao_web()

    ultimo_erro = None

    for url_produto in urls:

        logger.info(
            "Tentando fallback Web: %s",
            url_produto,
        )

        try:

            response = session.get(
                url_produto,
                allow_redirects=True,
                timeout=HTTP_TIMEOUT,
            )

        except requests.RequestException as erro:

            ultimo_erro = erro

            logger.warning(
                "Erro HTTP no fallback Web: %s",
                erro,
            )

            continue

        url_final = response.url or ""

        logger.info(
            "Fallback Web HTTP %d | URL final: %s",
            response.status_code,
            url_final,
        )

        # ----------------------------------------------------
        # NÃO ACEITAR ACCOUNT-VERIFICATION
        # ----------------------------------------------------

        if (
            "/gz/account-verification"
            in url_final.lower()
        ):

            logger.warning(
                "Mercado Livre redirecionou para "
                "account-verification. "
                "A página não contém dados confiáveis "
                "do produto."
            )

            ultimo_erro = MercadoLivreAPIError(
                "Mercado Livre exigiu verificação "
                "de conta para acesso Web."
            )

            continue

        if response.status_code != 200:

            ultimo_erro = MercadoLivreAPIError(
                "Erro ao acessar página Web "
                f"do produto: HTTP {response.status_code}"
            )

            continue

        html = response.text or ""

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # ----------------------------------------------------
        # JSON-LD
        # ----------------------------------------------------

        json_ld = _extrair_json_ld(
            soup
        )

        # ----------------------------------------------------
        # TÍTULO
        # ----------------------------------------------------

        titulo = None

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

        if not titulo:

            titulo = json_ld.get(
                "name"
            )

        if not titulo:

            meta_title = soup.find(
                "meta",
                property="og:title",
            )

            if meta_title:

                titulo = (
                    meta_title.get(
                        "content"
                    )
                    or None
                )

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        image_url = None

        meta_image = soup.find(
            "meta",
            property="og:image",
        )

        if meta_image:

            image_url = (
                meta_image.get(
                    "content"
                )
                or None
            )

        if not image_url:

            imagem_json = json_ld.get(
                "image"
            )

            if isinstance(
                imagem_json,
                list,
            ):

                if imagem_json:
                    image_url = (
                        imagem_json[0]
                    )

            elif isinstance(
                imagem_json,
                str,
            ):

                image_url = imagem_json

        if not image_url:

            img = soup.find(
                "img",
                class_="ui-pdp-image",
            )

            if img:

                image_url = (
                    img.get("src")
                    or img.get(
                        "data-zoom"
                    )
                    or img.get(
                        "data-src"
                    )
                )

        # ----------------------------------------------------
        # PREÇO
        # ----------------------------------------------------

        preco = None

        meta_price = soup.find(
            "meta",
            itemprop="price",
        )

        if meta_price:

            valor = meta_price.get(
                "content"
            )

            try:

                if valor:
                    preco = float(
                        valor
                    )

            except (
                ValueError,
                TypeError,
            ):
                pass

        if preco is None:

            offers = json_ld.get(
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

                try:

                    if valor is not None:
                        preco = float(
                            valor
                        )

                except (
                    ValueError,
                    TypeError,
                ):
                    pass

        # ----------------------------------------------------
        # VALIDAÇÃO
        # ----------------------------------------------------

        if not any(
            (
                titulo,
                image_url,
                preco,
            )
        ):

            logger.warning(
                "Página Web acessível, porém "
                "não contém dados do produto."
            )

            ultimo_erro = MercadoLivreAPIError(
                "Página do Mercado Livre não "
                "contém dados do produto."
            )

            continue

        return {
            "id": item_id,
            "title": (
                titulo
                or "Produto Mercado Livre"
            ),
            "price": preco,
            "original_price": None,
            "pictures": (
                [
                    {
                        "secure_url": image_url
                    }
                ]
                if image_url
                else []
            ),
            "permalink": (
                url_produto
            ),
            "condition": (
                "new"
            ),
        }

    if ultimo_erro:

        if isinstance(
            ultimo_erro,
            MercadoLivreAPIError,
        ):
            raise ultimo_erro

        raise MercadoLivreAPIError(
            str(ultimo_erro)
        )

    raise MercadoLivreAPIError(
        "Não foi possível obter os dados "
        "do produto via Web."
    )


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

        mensagem = str(
            erro
        )

        if "403" not in mensagem:

            raise

        logger.warning(
            "API do Mercado Livre falhou para %s: %s",
            item_id,
            mensagem,
        )

        logger.warning(
            "API bloqueada com HTTP 403. "
            "Tentando fallback Web para %s.",
            item_id,
        )

        return _consultar_item_via_web(
            item_id
        )


# ============================================================
# BUSCAR POR ID
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

        return None

    item_id_retorno = (
        produto_api.get("id")
        or item_id_normalizado
    )

    seller_id = (
        produto_api.get(
            "seller_id"
        )
    )

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    image_url = None

    pictures = (
        produto_api.get(
            "pictures"
        )
        or []
    )

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

    if not image_url:

        image_url = (
            produto_api.get(
                "thumbnail"
            )
        )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    price = produto_api.get(
        "price"
    )

    original_price = (
        produto_api.get(
            "original_price"
        )
    )

    discount = None

    try:

        if (
            price is not None
            and original_price is not None
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

    except (
        ValueError,
        TypeError,
    ):
        pass

    # --------------------------------------------------------
    # VENDAS
    # --------------------------------------------------------

    sales = produto_api.get(
        "sold_quantity"
    )

    # --------------------------------------------------------
    # SELLER
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    product_link = (
        produto_api.get(
            "permalink"
        )
    )

    # --------------------------------------------------------
    # PRODUTO FINAL
    # --------------------------------------------------------

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

        "mercadolivreData": (
            produto_api
        ),
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

    link = (
        str(link)
        .strip()
    )

    # --------------------------------------------------------
    # RESOLVER
    # --------------------------------------------------------

    url_final, item_id = (
        resolver_link_e_extrair_id(
            link
        )
    )

    logger.info(
        "Produto identificado: %s",
        item_id,
    )

    # --------------------------------------------------------
    # BUSCAR
    # --------------------------------------------------------

    produto = buscar_produto_por_ids(
        item_id=item_id
    )

    if not produto:

        raise MercadoLivreAPIError(
            "O produto não foi encontrado."
        )

    # --------------------------------------------------------
    # PRESERVAR LINK ORIGINAL
    # --------------------------------------------------------

    produto.update(
        {
            "manualAffiliateLink": link,

            "affiliateLink": link,

            "originalAffiliateLink": link,

            "resolvedProductLink": url_final,

            "itemId": item_id,
        }
    )

    return produto
