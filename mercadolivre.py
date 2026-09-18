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
# SESSÃO E REQUISIÇÕES HTTP
# ============================================================

def _obter_sessao() -> requests.Session:
    """
    Cria uma sessão HTTP com headers semelhantes aos de um
    navegador Chrome.

    O access token agora é obtido centralmente através do
    módulo mercadolivre_oauth.py.

    O módulo OAuth consulta o Supabase e, quando necessário,
    realiza automaticamente o refresh do token.
    """
    session = requests.Session()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
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

    # ========================================================
    # ACCESS TOKEN DINÂMICO
    # ========================================================
    #
    # Não depender exclusivamente de:
    #
    # MERCADOLIVRE_ACCESS_TOKEN
    #
    # O módulo OAuth busca o token no Supabase e executa
    # refresh automático quando necessário.
    #
    # ========================================================

    try:
        access_token = obter_access_token_mercadolivre()

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
# EXTRAÇÃO DE ITEM ID (MLB)
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:
    """
    Extrai um Item ID válido do Mercado Livre
    (formato MLB + 8 a 10 dígitos).
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

                if key in params:

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
    """Valida e extrai o Item ID de uma string fornecida."""
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
# RESOLVER LINK E ACESSAR VITRINE
# ============================================================

def resolver_link_e_extrair_id(
    link: str,
) -> Tuple[str, str]:
    """
    Resolve o link de redirecionamento.

    Caso o link caia em uma vitrine/perfil social (/social/),
    o script varre o HTML para encontrar o produto de destino.
    """
    link = link.strip()

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

        logger.info(
            "HTTP ao resolver link: %d",
            response.status_code,
        )

        if response.status_code >= 400:

            raise MercadoLivreAPIError(
                "Mercado Livre retornou "
                f"HTTP {response.status_code} "
                "ao resolver o link."
            )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            f"Erro ao resolver link do Mercado Livre: {erro}"
        ) from erro

    url_final = response.url

    logger.info(
        "URL final resolvida: %s",
        url_final,
    )

    if not url_final:

        raise MercadoLivreAPIError(
            "O Mercado Livre não retornou uma URL final."
        )

    # ========================================================
    # 1. TENTA EXTRAIR MLB DIRETAMENTE DA URL
    # ========================================================

    item_id = extrair_item_id_da_string(
        url_final
    )

    if item_id:

        logger.info(
            "Item ID extraído diretamente da URL final: %s",
            item_id,
        )

        return (
            url_final,
            item_id,
        )

    # ========================================================
    # 2. VITRINE / SOCIAL
    # ========================================================

    if (
        "/social/" in url_final
        and response.text
    ):

        logger.info(
            "Página de vitrine detectada (/social/). "
            "Procurando produtos na vitrine..."
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # LINKS <a>
        # ----------------------------------------------------

        for tag_a in soup.find_all(
            "a",
            href=True,
        ):

            href = tag_a["href"]

            id_encontrado = (
                extrair_item_id_da_string(
                    href
                )
            )

            if id_encontrado:

                logger.info(
                    "Produto encontrado nos links da vitrine: %s",
                    id_encontrado,
                )

                return (
                    url_final,
                    id_encontrado,
                )

        # ----------------------------------------------------
        # REGEX NO HTML
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
                "Produto encontrado via Regex "
                "no corpo da vitrine: %s",
                id_encontrado,
            )

            return (
                url_final,
                id_encontrado,
            )

    raise MercadoLivreAPIError(
        "Não foi possível extrair nenhum produto válido "
        "do link ou da vitrine informada."
    )


# ============================================================
# CONSULTA DE PRODUTOS - API
# ============================================================

def _api_get(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executa uma chamada GET na API do Mercado Livre.

    A sessão obtém o access token atual através do OAuth.
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

        # IMPORTANTE:
        # Não registrar headers porque podem conter
        # Authorization.
        #
        # Também limitamos o corpo da resposta.
        #
        logger.error(
            "Mercado Livre retornou HTTP %d.",
            response.status_code,
        )

        raise MercadoLivreAPIError(
            f"Mercado Livre respondeu HTTP "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

    try:

        return response.json()

    except ValueError as erro:

        raise MercadoLivreAPIError(
            "A API do Mercado Livre retornou "
            "uma resposta em formato inválido "
            "(não JSON)."
        ) from erro


# ============================================================
# FALLBACK WEB SCRAPING
# ============================================================

def _consultar_item_via_web(
    item_id: str,
) -> Dict[str, Any]:
    """
    Fallback:

    Raspa as informações do produto diretamente do
    HTML/JSON-LD do anúncio caso a API do Mercado Livre
    bloqueie com 403.
    """
    url_produto = (
        f"https://produto.mercadolivre.com.br/{item_id}"
    )

    logger.info(
        "Iniciando fallback de raspagem Web do anúncio: %s",
        url_produto,
    )

    session = _obter_sessao()

    try:

        res = session.get(
            url_produto,
            timeout=30,
        )

        if res.status_code != 200:

            raise MercadoLivreAPIError(
                "Erro ao acessar página web "
                f"do produto: HTTP {res.status_code}"
            )

        soup = BeautifulSoup(
            res.text,
            "html.parser",
        )

        # ====================================================
        # TÍTULO
        # ====================================================

        titulo_elem = soup.find(
            "h1",
            class_="ui-pdp-title",
        )

        titulo = (
            titulo_elem.text.strip()
            if titulo_elem
            else "Produto Mercado Livre"
        )

        # ====================================================
        # IMAGEM PRINCIPAL
        # ====================================================

        imageUrl = None

        img_elem = soup.find(
            "img",
            class_="ui-pdp-image",
        )

        if img_elem:

            imageUrl = (
                img_elem.get("src")
                or img_elem.get("data-zoom")
            )

        # ====================================================
        # PREÇO
        # ====================================================

        preco = None

        preco_elem = soup.find(
            "meta",
            itemprop="price",
        )

        if (
            preco_elem
            and preco_elem.get("content")
        ):

            try:

                preco = float(
                    preco_elem["content"]
                )

            except ValueError:

                pass

        # ====================================================
        # JSON-LD
        # ====================================================

        if not preco:

            scripts_json = soup.find_all(
                "script",
                type="application/ld+json",
            )

            for script in scripts_json:

                if not script.string:
                    continue

                try:

                    dados = json.loads(
                        script.string
                    )

                    if isinstance(
                        dados,
                        list,
                    ):

                        if not dados:
                            continue

                        dados = dados[0]

                    if not isinstance(
                        dados,
                        dict,
                    ):

                        continue

                    offers = dados.get(
                        "offers",
                        {},
                    )

                    if isinstance(
                        offers,
                        list,
                    ):

                        if not offers:
                            continue

                        offers = offers[0]

                    if not isinstance(
                        offers,
                        dict,
                    ):

                        continue

                    valor_ofertado = (
                        offers.get("price")
                    )

                    if valor_ofertado:

                        preco = float(
                            valor_ofertado
                        )

                        break

                except Exception:

                    continue

        return {
            "id": item_id,
            "title": titulo,
            "price": preco,
            "pictures": (
                [{"secure_url": imageUrl}]
                if imageUrl
                else []
            ),
            "permalink": url_produto,
            "condition": "new",
        }

    except MercadoLivreAPIError:

        raise

    except Exception as erro:

        raise MercadoLivreAPIError(
            "Falha ao raspar dados web do "
            f"produto {item_id}: {erro}"
        ) from erro


# ============================================================
# CONSULTAR ITEM
# ============================================================

def _consultar_item(
    item_id: str,
) -> Dict[str, Any]:
    """
    Consulta o produto via API.

    Se houver HTTP 403, utiliza fallback Web Scraping.
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
            "Formato de Item ID inválido para API: "
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
                "Ativando fallback de raspagem Web "
                "para o item %s...",
                item_id,
            )

            return _consultar_item_via_web(
                item_id
            )

        raise


# ============================================================
# BUSCAR E FORMATAR DADOS DO PRODUTO
# ============================================================

def buscar_produto_por_ids(
    item_id: Any,
    shop_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Consulta os detalhes do produto e formata para o
    padrão esperado pelo bot.
    """
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
    # IMAGEM PRINCIPAL
    # ========================================================

    image_url = None

    pictures = (
        produto_api.get("pictures")
        or []
    )

    if (
        pictures
        and isinstance(
            pictures[0],
            dict,
        )
    ):

        image_url = (
            pictures[0].get(
                "secure_url"
            )
            or pictures[0].get(
                "url"
            )
        )

    # ========================================================
    # PREÇO E DESCONTOS
    # ========================================================

    price = produto_api.get(
        "price"
    )

    original_price = produto_api.get(
        "original_price"
    )

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

    product_link = produto_api.get(
        "permalink"
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

    rating_star = (
        seller_reputation.get(
            "seller_reputation_level"
        )
        if isinstance(
            seller_reputation,
            dict,
        )
        else None
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
            or "Produto"
        ),

        "title": (
            produto_api.get(
                "title"
            )
            or "Produto"
        ),

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
    Função principal:

    1. Resolve o link encurtado/vitrine.
    2. Extrai o MLB do produto.
    3. Busca os dados do produto.
    4. Preserva o link de afiliado original.
    """
    if not link:

        raise MercadoLivreAPIError(
            "Link de entrada está vazio."
        )

    link = link.strip()

    # ========================================================
    # RESOLVER LINK
    # ========================================================

    url_final, item_id = (
        resolver_link_e_extrair_id(
            link
        )
    )

    # ========================================================
    # BUSCAR PRODUTO
    # ========================================================

    produto = buscar_produto_por_ids(
        item_id=item_id
    )

    if not produto:

        raise MercadoLivreAPIError(
            "O produto não foi encontrado "
            "na API do Mercado Livre."
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

    return produto
