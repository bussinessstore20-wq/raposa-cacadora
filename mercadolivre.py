import logging
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import requests

logger = logging.getLogger(__name__)

MERCADO_LIVRE_API_URL = "https://api.mercadolibre.com"


class MercadoLivreAPIError(Exception):
    """Erro relacionado à consulta de produtos do Mercado Livre."""


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def _resolver_link(link: str):
    link = str(link or "").strip()
    if not link:
        raise MercadoLivreAPIError("Link do Mercado Livre está vazio.")

    try:
        response = requests.get(
            link,
            allow_redirects=True,
            timeout=30,
            headers=_HEADERS,
        )
    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro ao resolver link do Mercado Livre: {erro}"
        ) from erro

    if response.status_code >= 400:
        raise MercadoLivreAPIError(
            f"Mercado Livre retornou HTTP {response.status_code} ao resolver o link."
        )

    return response.url or link, response.text or ""


def _normalizar_item_id(valor: str) -> str | None:
    if not valor:
        return None

    valor = unquote(str(valor)).upper()
    match = re.search(r"\bMLB[-_ ]?(\d{6,})\b", valor)
    if not match:
        return None

    return f"MLB{match.group(1)}"


def _extrair_item_id(url: str, html: str = "") -> str:
    """Extrai o ID mesmo quando ele não aparece diretamente no caminho da URL."""
    parsed = urlparse(url)
    partes = [
        parsed.path,
        parsed.query,
        parsed.fragment,
        unquote(url),
    ]

    # Primeiro procura explicitamente por item_id, wid, itemId etc.
    params = parse_qs(parsed.query)
    for chave in ("item_id", "itemId", "wid", "id"):
        for valor in params.get(chave, []):
            item_id = _normalizar_item_id(valor)
            if item_id:
                return item_id

    for texto in partes:
        item_id = _normalizar_item_id(texto)
        if item_id:
            return item_id

    # Algumas páginas do Mercado Livre escondem o ID no HTML/JSON inicial.
    # Procuramos o ID sem depender de um único nome de campo.
    if html:
        html_decodificado = unescape(unquote(html))
        padroes_html = [
            r'"(?:id|item_id|itemId|itemID|itemIdString)"\s*:\s*"?(MLB[-_ ]?\d{6,})',
            r'"(?:canonical|url|permalink)"\s*:\s*"[^"]*?(MLB[-_ ]?\d{6,})',
            r'(?:/|%2F)(MLB[-_ ]?\d{6,})(?:[/?#%&"\\]|$)',
            r'\b(MLB[-_ ]?\d{6,})\b',
        ]
        for padrao in padroes_html:
            match = re.search(padrao, html_decodificado, re.IGNORECASE)
            if match:
                item_id = _normalizar_item_id(match.group(1))
                if item_id:
                    return item_id

    raise MercadoLivreAPIError(
        "Não foi possível encontrar o ID do produto Mercado Livre no link."
    )


def buscar_produto_por_link(link: str) -> dict:
    """Consulta um item público do Mercado Livre e normaliza para o formato usado pela Raposa."""
    url_final, html = _resolver_link(link)
    item_id = _extrair_item_id(url_final, html)

    logger.info("Mercado Livre: consultando item %s", item_id)

    try:
        response = requests.get(
            f"{MERCADO_LIVRE_API_URL}/items/{item_id}",
            timeout=30,
            headers={
                "Accept": "application/json",
                "User-Agent": "RaposaCacadora/1.0",
            },
        )
    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro de conexão com o Mercado Livre: {erro}"
        ) from erro

    if response.status_code >= 400:
        raise MercadoLivreAPIError(
            f"Mercado Livre retornou HTTP {response.status_code} ao consultar {item_id}."
        )

    try:
        item = response.json()
    except ValueError as erro:
        raise MercadoLivreAPIError(
            "O Mercado Livre retornou uma resposta inválida."
        ) from erro

    if not isinstance(item, dict) or not item.get("id"):
        raise MercadoLivreAPIError("Produto não encontrado no Mercado Livre.")

    preco = float(item.get("price") or 0)
    preco_original = float(item.get("original_price") or 0)
    desconto = 0.0
    if preco_original > preco > 0:
        desconto = ((preco_original - preco) / preco_original) * 100

    pictures = item.get("pictures") or []
    image_url = ""
    for picture in pictures:
        if isinstance(picture, dict):
            image_url = str(
                picture.get("secure_url")
                or picture.get("url")
                or ""
            ).strip()
            if image_url:
                break

    seller = item.get("seller") or {}
    shop_name = str(
        seller.get("nickname")
        or item.get("seller_id")
        or "Mercado Livre"
    )

    produto = {
        "productName": item.get("title") or "Produto Mercado Livre",
        "itemId": item.get("id") or item_id,
        "shopId": item.get("seller_id"),
        "price": preco,
        "priceMin": preco,
        "priceMax": preco,
        "originalPrice": preco_original,
        "priceDiscountRate": desconto,
        "ratingStar": 0,
        "sales": item.get("sold_quantity") or 0,
        "shopName": shop_name,
        "imageUrl": image_url,
        "productLink": item.get("permalink") or url_final,
        "offerLink": item.get("permalink") or url_final,
        "manualAffiliateLink": link,
        "affiliateLink": link,
        "marketplace": "mercadolivre",
    }

    logger.info(
        "Produto Mercado Livre encontrado: %s | item=%s",
        produto["productName"],
        produto["itemId"],
    )

    return produto
