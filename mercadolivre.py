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


# ============================================================
# ERRO DA API
# ============================================================

class MercadoLivreAPIError(Exception):
    """Erro relacionado à API do Mercado Livre."""


# ============================================================
# SESSÃO HTTP
# ============================================================

def _obter_sessao() -> requests.Session:
    """
    Cria uma sessão HTTP semelhante a um navegador.

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
    Extrai um Item ID do Mercado Livre.

    Exemplos aceitos:

        MLB5459371754
        MLB-5459371754
        MLB_5459371754
        https://produto.mercadolivre.com.br/MLB5459371754
    """

    if not conteudo:
        return None

    texto = str(
        conteudo
    ).strip()

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

        parsed = urlparse(
            texto
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

                val = params[key][0]

                m = re.search(
                    r"MLB[-_]?(\d{8,10})",
                    val,
                    re.IGNORECASE,
                )

                if m:

                    return (
                        f"MLB{m.group(1)}"
                        .upper()
                    )

    except Exception:

        pass

    return None


def extrair_item_id(
    valor: str,
) -> str:
    """
    Valida e extrai o Item ID.
    """

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
    Resolve o link do Mercado Livre.

    Suporta:

    - meli.la
    - links normais do Mercado Livre
    - links de produto
    - vitrines /social/
    """

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
            timeout=30,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao resolver link do "
            f"Mercado Livre: {erro}"
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
        or ""
    ).strip()

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    if not url_final:

        raise MercadoLivreAPIError(
            "O Mercado Livre não retornou "
            "uma URL final."
        )

    # ========================================================
    # TENTA EXTRAIR MLB DA URL
    # ========================================================

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

    # ========================================================
    # TENTA EXTRAIR MLB DO LINK ORIGINAL
    # ========================================================

    item_id = (
        extrair_item_id_da_string(
            link
        )
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
        "/social/" in url_final.lower()
        and response.text
    ):

        logger.info(
            "Página de vitrine detectada. "
            "Procurando produtos..."
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
                str(
                    tag_a.get(
                        "href"
                    )
                    or ""
                )
            )

            id_encontrado = (
                extrair_item_id_da_string(
                    href
                )
            )

            if id_encontrado:

                logger.info(
                    "Produto encontrado "
                    "nos links da vitrine: %s",
                    id_encontrado,
                )

                return (
                    url_final,
                    id_encontrado,
                )

        # ----------------------------------------------------
        # REGEX HTML
        # ----------------------------------------------------

        matches = re.findall(
            r"MLB[-_]?(\d{8,10})",
            response.text,
            re.IGNORECASE,
        )

        if matches:

            id_encontrado = (
                f"MLB{matches[0]}"
                .upper()
            )

            logger.info(
                "Produto encontrado via "
                "Regex no HTML: %s",
                id_encontrado,
            )

            return (
                url_final,
                id_encontrado,
            )

    raise MercadoLivreAPIError(
        "Não foi possível extrair nenhum "
        "produto válido do link."
    )


# ============================================================
# API
# ============================================================

def _api_get(
    endpoint: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Dict[str, Any]:
    """
    Executa GET na API do Mercado Livre.
    """

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
            timeout=30,
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

        logger.error(
            "Mercado Livre retornou HTTP %d.",
            response.status_code,
        )

        raise MercadoLivreAPIError(
            "Mercado Livre respondeu HTTP "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

    try:

        dados = response.json()

    except ValueError as erro:

        raise MercadoLivreAPIError(
            "A API do Mercado Livre retornou "
            "uma resposta inválida."
        ) from erro

    if not isinstance(
        dados,
        dict,
    ):

        raise MercadoLivreAPIError(
            "A API do Mercado Livre não retornou "
            "um objeto JSON."
        )

    return dados


# ============================================================
# HELPERS DO SCRAPING
# ============================================================

def _meta_content(
    soup: BeautifulSoup,
    *,
    property_name: Optional[str] = None,
    name: Optional[str] = None,
    itemprop: Optional[str] = None,
) -> Optional[str]:
    """
    Busca conteúdo de meta tag.
    """

    attrs = {}

    if property_name:
        attrs["property"] = property_name

    if name:
        attrs["name"] = name

    if itemprop:
        attrs["itemprop"] = itemprop

    if not attrs:
        return None

    tag = soup.find(
        "meta",
        attrs=attrs,
    )

    if not tag:
        return None

    valor = (
        tag.get("content")
        or ""
    ).strip()

    return valor or None


def _converter_preco(
    valor: Any,
) -> Optional[float]:
    """
    Converte preço para float.
    """

    if valor is None:
        return None

    if isinstance(
        valor,
        (int, float),
    ):

        return float(
            valor
        )

    texto = (
        str(valor)
        .strip()
    )

    if not texto:
        return None

    # Remove símbolos
    texto = re.sub(
        r"[^\d,.\-]",
        "",
        texto,
    )

    if not texto:
        return None

    # Exemplo:
    # 1.299,90 -> 1299.90
    if (
        "," in texto
        and "." in texto
    ):

        texto = (
            texto
            .replace(
                ".",
                "",
            )
            .replace(
                ",",
                ".",
            )
        )

    elif "," in texto:

        texto = (
            texto.replace(
                ",",
                ".",
            )
        )

    try:

        return float(
            texto
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


# ============================================================
# FALLBACK WEB
# ============================================================

def _consultar_item_via_web(
    item_id: str,
) -> Dict[str, Any]:
    """
    Fallback para HTTP 403 da API.

    Tenta extrair dados de:

    - JSON-LD
    - OpenGraph
    - meta tags
    - HTML
    - imagens
    - preço
    """

    url_produto = (
        "https://produto.mercadolivre.com.br/"
        f"{item_id}"
    )

    logger.info(
        "Iniciando fallback de raspagem Web: %s",
        url_produto,
    )

    session = _obter_sessao()

    try:

        res = session.get(
            url_produto,
            timeout=30,
            allow_redirects=True,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao acessar página Web "
            f"do produto: {erro}"
        ) from erro

    logger.info(
        "Fallback Web HTTP %d | URL final: %s",
        res.status_code,
        res.url,
    )

    if res.status_code != 200:

        raise MercadoLivreAPIError(
            "Erro ao acessar página Web "
            f"do produto: HTTP {res.status_code}"
        )

    html = res.text

    if not html:

        raise MercadoLivreAPIError(
            "Mercado Livre retornou HTML vazio."
        )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # ========================================================
    # VARIÁVEIS
    # ========================================================

    titulo = None
    preco = None
    original_price = None
    image_url = None
    description = None
    condition = None
    category_id = None
    seller_name = None

    # ========================================================
    # JSON-LD
    # ========================================================

    scripts_json = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts_json:

        conteudo = (
            script.string
            or script.get_text(
                strip=True
            )
        )

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
            list,
        ):

            candidatos = dados

        elif isinstance(
            dados,
            dict,
        ):

            # Alguns JSON-LD usam @graph
            graph = dados.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):

                candidatos = graph

            else:

                candidatos = [
                    dados
                ]

        else:

            candidatos = []

        for dados_item in candidatos:

            if not isinstance(
                dados_item,
                dict,
            ):

                continue

            # ------------------------------------------------
            # TÍTULO
            # ------------------------------------------------

            if not titulo:

                titulo = (
                    dados_item.get(
                        "name"
                    )
                    or dados_item.get(
                        "headline"
                    )
                )

            # ------------------------------------------------
            # DESCRIÇÃO
            # ------------------------------------------------

            if not description:

                description = (
                    dados_item.get(
                        "description"
                    )
                )

            # ------------------------------------------------
            # IMAGEM
            # ------------------------------------------------

            if not image_url:

                imagem = dados_item.get(
                    "image"
                )

                if isinstance(
                    imagem,
                    list,
                ):

                    for img in imagem:

                        if isinstance(
                            img,
                            dict,
                        ):

                            img = (
                                img.get(
                                    "url"
                                )
                            )

                        if img:

                            image_url = str(
                                img
                            )

                            break

                elif isinstance(
                    imagem,
                    dict,
                ):

                    image_url = (
                        imagem.get(
                            "url"
                        )
                    )

                elif imagem:

                    image_url = str(
                        imagem
                    )

            # ------------------------------------------------
            # OFERTAS
            # ------------------------------------------------

            offers = dados_item.get(
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

                    preco = (
                        _converter_preco(
                            offers.get(
                                "price"
                            )
                        )
                    )

                if original_price is None:

                    original_price = (
                        _converter_preco(
                            offers.get(
                                "highPrice"
                            )
                        )
                    )

                if not condition:

                    condition = (
                        offers.get(
                            "itemCondition"
                        )
                    )

            # ------------------------------------------------
            # VENDEDOR
            # ------------------------------------------------

            brand = dados_item.get(
                "brand"
            )

            if isinstance(
                brand,
                dict,
            ):

                if not seller_name:

                    seller_name = (
                        brand.get(
                            "name"
                        )
                    )

    # ========================================================
    # META TAGS
    # ========================================================

    if not titulo:

        titulo = (
            _meta_content(
                soup,
                property_name="og:title",
            )
            or _meta_content(
                soup,
                name="twitter:title",
            )
        )

    if not image_url:

        image_url = (
            _meta_content(
                soup,
                property_name="og:image",
            )
            or _meta_content(
                soup,
                name="twitter:image",
            )
        )

    if not description:

        description = (
            _meta_content(
                soup,
                property_name="og:description",
            )
            or _meta_content(
                soup,
                name="description",
            )
        )

    if preco is None:

        preco = _converter_preco(
            _meta_content(
                soup,
                itemprop="price",
            )
            or _meta_content(
                soup,
                property_name=(
                    "product:price:amount"
                ),
            )
        )

    currency_id = (
        _meta_content(
            soup,
            itemprop="priceCurrency",
        )
        or _meta_content(
            soup,
            property_name=(
                "product:price:currency"
            ),
        )
        or "BRL"
    )

    # ========================================================
    # HTML - TÍTULO
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
    # HTML - IMAGEM
    # ========================================================

    if not image_url:

        img_elem = soup.find(
            "img",
            class_=re.compile(
                r"ui-pdp-image",
                re.IGNORECASE,
            ),
        )

        if img_elem:

            image_url = (
                img_elem.get(
                    "src"
                )
                or img_elem.get(
                    "data-src"
                )
                or img_elem.get(
                    "data-zoom"
                )
            )

    # ========================================================
    # QUALQUER IMG VÁLIDA
    # ========================================================

    if not image_url:

        for img in soup.find_all(
            "img"
        ):

            candidatos = [
                img.get("src"),
                img.get("data-src"),
                img.get("data-zoom"),
                img.get("data-lazy"),
            ]

            for candidato in candidatos:

                if (
                    candidato
                    and str(
                        candidato
                    ).startswith(
                        "http"
                    )
                ):

                    image_url = (
                        str(
                            candidato
                        )
                    )

                    break

            if image_url:
                break

    # ========================================================
    # HTML - PREÇO
    # ========================================================

    if preco is None:

        seletores_preco = [
            "ui-pdp-price__second-line__fraction",
            "andes-money-amount__fraction",
        ]

        for classe in seletores_preco:

            preco_elem = soup.find(
                class_=re.compile(
                    re.escape(classe),
                    re.IGNORECASE,
                )
            )

            if not preco_elem:
                continue

            texto_preco = (
                preco_elem.get_text(
                    " ",
                    strip=True,
                )
            )

            preco_tentativa = (
                _converter_preco(
                    texto_preco
                )
            )

            if preco_tentativa is not None:

                preco = (
                    preco_tentativa
                )

                break

    # ========================================================
    # LINK FINAL
    # ========================================================

    permalink = (
        res.url
        or url_produto
    )

    # ========================================================
    # TÍTULO FINAL
    # ========================================================

    if titulo:

        titulo = (
            str(titulo)
            .strip()
        )

    if not titulo:

        titulo = (
            f"Produto {item_id}"
        )

    # ========================================================
    # LOG DETALHADO
    # ========================================================

    logger.info(
        "Fallback extraído | "
        "item=%s | "
        "titulo=%s | "
        "preco=%s | "
        "imagem=%s | "
        "descricao=%s",
        item_id,
        titulo,
        preco,
        bool(image_url),
        bool(description),
    )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "id": item_id,

        "title": titulo,

        "price": preco,

        "original_price": original_price,

        "currency_id": currency_id,

        "pictures": (
            [
                {
                    "secure_url": image_url,
                    "url": image_url,
                }
            ]
            if image_url
            else []
        ),

        "permalink": permalink,

        "condition": (
            condition
            or "new"
        ),

        "description": description,

        "category_id": category_id,

        "seller_name": seller_name,
    }


# ============================================================
# CONSULTAR ITEM
# ============================================================

def _consultar_item(
    item_id: str,
) -> Dict[str, Any]:
    """
    Consulta primeiro a API.

    Se a API retornar 403,
    utiliza fallback Web.
    """

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

        if "403" in str(erro):

            logger.warning(
                "API bloqueada com HTTP 403. "
                "Ativando fallback Web para "
                "o item %s...",
                item_id,
            )

            return (
                _consultar_item_via_web(
                    item_id
                )
            )

        raise


# ============================================================
# BUSCAR PRODUTO POR ID
# ============================================================

def buscar_produto_por_ids(
    item_id: Any,
    shop_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Consulta os detalhes e transforma o resultado
    no formato utilizado pelo bot.
    """

    logger.info(
        "Consultando produto: itemId=%s",
        item_id,
    )

    item_id_normalizado = (
        extrair_item_id(
            str(item_id)
        )
    )

    produto_api = (
        _consultar_item(
            item_id_normalizado
        )
    )

    if not produto_api:

        logger.warning(
            "Nenhum dado retornado para %s",
            item_id_normalizado,
        )

        return None

    item_id_retorno = (
        produto_api.get(
            "id"
        )
        or item_id_normalizado
    )

    seller_id = (
        produto_api.get(
            "seller_id"
        )
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

    price = produto_api.get(
        "price"
    )

    original_price = (
        produto_api.get(
            "original_price"
        )
    )

    price_discount_rate = None

    try:

        if (
            original_price
            and price
            and float(
                original_price
            ) > 0
            and float(price)
            < float(
                original_price
            )
        ):

            price_discount_rate = round(
                (
                    (
                        float(
                            original_price
                        )
                        - float(price)
                    )
                    / float(
                        original_price
                    )
                )
                * 100,
                2,
            )

    except (
        TypeError,
        ValueError,
    ):

        price_discount_rate = None

    # ========================================================
    # VENDAS
    # ========================================================

    sales = (
        produto_api.get(
            "sold_quantity"
        )
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
    # TÍTULO
    # ========================================================

    titulo = (
        produto_api.get(
            "title"
        )
        or produto_api.get(
            "name"
        )
        or f"Produto {item_id_normalizado}"
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

        "productName": titulo,

        "title": titulo,

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

        "shopName": (
            produto_api.get(
                "seller_name"
            )
        ),

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

        "mercadolivreData": (
            produto_api
        ),
    }

    logger.info(
        "Produto processado com sucesso | "
        "nome=%s | "
        "item=%s | "
        "preco=%s | "
        "imagem=%s | "
        "link=%s",
        produto.get(
            "productName"
        ),
        produto.get(
            "itemId"
        ),
        produto.get(
            "price"
        ),
        bool(
            produto.get(
                "imageUrl"
            )
        ),
        produto.get(
            "productLink"
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
    Função principal.

    1. Resolve o link.
    2. Extrai o MLB.
    3. Busca o produto.
    4. Mantém o link de afiliado original.
    """

    if not link:

        raise MercadoLivreAPIError(
            "Link de entrada está vazio."
        )

    link = (
        str(link)
        .strip()
    )

    # ========================================================
    # RESOLVER LINK
    # ========================================================

    url_final, item_id = (
        resolver_link_e_extrair_id(
            link
        )
    )

    logger.info(
        "Link resolvido | "
        "original=%s | "
        "final=%s | "
        "item=%s",
        link,
        url_final,
        item_id,
    )

    # ========================================================
    # BUSCAR PRODUTO
    # ========================================================

    produto = (
        buscar_produto_por_ids(
            item_id=item_id
        )
    )

    if not produto:

        raise MercadoLivreAPIError(
            "O produto não foi encontrado "
            "no Mercado Livre."
        )

    # ========================================================
    # PRESERVAR LINK DE AFILIADO
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

    # ========================================================
    # LOG FINAL
    # ========================================================

    logger.info(
        "Produto encontrado | "
        "nome=%s | "
        "item=%s | "
        "preco=%s | "
        "imagem=%s | "
        "link=%s",
        produto.get(
            "productName"
        ),
        produto.get(
            "itemId"
        ),
        produto.get(
            "price"
        ),
        bool(
            produto.get(
                "imageUrl"
            )
        ),
        produto.get(
            "productLink"
        ),
    )

    return produto
