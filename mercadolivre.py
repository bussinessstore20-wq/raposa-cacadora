import logging
import os
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




def _extrair_dados_da_pagina(html: str, url_final: str, item_id: str) -> dict:
    if not html:
        raise MercadoLivreAPIError(f"Mercado Livre bloqueou a API para {item_id} e não foi possível ler a página.")
    html_decodificado = unescape(unquote(html))
    dados = {}
    def _primeiro(*valores):
        for valor in valores:
            if valor is not None:
                valor = unescape(str(valor)).strip()
                if valor:
                    return valor
        return ""

    for bloco in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_decodificado, re.IGNORECASE | re.DOTALL):
        try:
            import json
            obj = json.loads(bloco.strip())
        except (ValueError, TypeError):
            continue
        for item in (obj if isinstance(obj, list) else [obj]):
            if isinstance(item, dict) and str(item.get("@type", "")).lower() in {"product", "offer"}:
                dados["title"] = dados.get("title") or item.get("name")
                dados["image"] = dados.get("image") or item.get("image")
                dados["url"] = dados.get("url") or item.get("url")

    if not dados.get("title"):
        match = re.search(r'<h1[^>]*>(.*?)</h1>', html_decodificado, re.IGNORECASE | re.DOTALL)
        if match:
            dados["title"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()

    moeda_re = r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)"
    for padrao in (
        rf"(?:de|era|antes)\s*:?\s*(?:<[^>]+>\s*)*{moeda_re}",
        rf"(?:de|era|antes)[^R$]{{0,120}}{moeda_re}",
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE | re.DOTALL)
        if match:
            dados["original_price"] = match.group(1)
            break

    for padrao in (
        rf"(?:por|agora|oferta|preço atual|preco atual)\s*:?\s*(?:<[^>]+>\s*)*{moeda_re}",
        rf"(?:por|agora|oferta|preço atual|preco atual)[^R$]{{0,120}}{moeda_re}",
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE | re.DOTALL)
        if match:
            dados["price"] = match.group(1)
            break

    valores = []
    for match in re.finditer(r'class=["\'][^"\']*andes-money-amount__fraction[^"\']*["\'][^>]*>\s*([0-9.]+)\s*<', html_decodificado, re.IGNORECASE):
        trecho = html_decodificado[max(0, match.start() - 900):match.end() + 500]
        cents = re.search(r'class=["\'][^"\']*andes-money-amount__cents[^"\']*["\'][^>]*>\s*([0-9]{1,2})\s*<', trecho, re.IGNORECASE)
        cent = cents.group(1) if cents else "00"
        try:
            valores.append(float(match.group(1).replace(".", "") + "." + cent.zfill(2)))
        except ValueError:
            pass

    if not valores:
        for valor in re.findall(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})", html_decodificado):
            try:
                valores.append(float(valor.replace(".", "").replace(",", ".")))
            except ValueError:
                pass

    def _float_moeda(valor):
        try:
            texto = str(valor or "").strip().replace("R$", "").replace(" ", "")
            if "," in texto and "." in texto:
                texto = texto.replace(".", "").replace(",", ".")
            elif "," in texto:
                texto = texto.replace(",", ".")
            return float(texto)
        except (TypeError, ValueError):
            return 0.0

    if not dados.get("price") and len(set(valores)) >= 2:
        dados["price"] = min(valores)
    if not dados.get("original_price") and len(set(valores)) >= 2:
        dados["original_price"] = max(valores)
    if not dados.get("price") and valores:
        dados["price"] = valores[0]

    for padrao in (
        r'"(?:sold_quantity|soldQuantity)"\s*:\s*([0-9]+)',
        r'(?:mais\s+de\s*)?([0-9][0-9.\s]*)\s*(?:unidades?\s*)?(?:vendid[oa]s?|vendas)',
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE)
        if match:
            try:
                dados["sales"] = int(re.sub(r"\D", "", match.group(1)))
                break
            except (ValueError, TypeError):
                pass

    if not dados.get("image"):
        match = re.search(r'"(?:secure_url|url)"\s*:\s*"(https?:[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html_decodificado, re.IGNORECASE)
        if match:
            dados["image"] = match.group(1)
    if not dados.get("url"):
        match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html_decodificado, re.IGNORECASE)
        if match:
            dados["url"] = match.group(1)

    preco = _float_moeda(dados.get("price"))
    preco_original = _float_moeda(dados.get("original_price"))
    desconto = ((preco_original - preco) / preco_original * 100) if preco_original > preco > 0 else 0.0

    produto = {
        "productName": _primeiro(dados.get("title"), f"Produto Mercado Livre {item_id}"),
        "itemId": item_id,
        "shopId": None,
        "price": preco,
        "priceMin": preco,
        "priceMax": preco,
        "originalPrice": preco_original,
        "priceDiscountRate": desconto,
        "ratingStar": 0,
        "sales": int(dados.get("sales") or 0),
        "shopName": "Mercado Livre",
        "imageUrl": _primeiro(dados.get("image")),
        "productLink": _primeiro(dados.get("url"), url_final),
        "offerLink": _primeiro(dados.get("url"), url_final),
        "manualAffiliateLink": url_final,
        "affiliateLink": url_final,
        "marketplace": "mercadolivre",
    }
    if not produto["productName"] and not produto["imageUrl"] and not produto["price"]:
        raise MercadoLivreAPIError(f"Mercado Livre bloqueou a API para {item_id} e a página não expôs dados do produto.")
    logger.info("Produto Mercado Livre obtido pela página: %s | item=%s | preço=%s | original=%s | vendas=%s", produto["productName"], item_id, preco, preco_original, produto["sales"])
    return produto

def buscar_produto_por_link(link: str) -> dict:
    """Consulta um item público do Mercado Livre e normaliza para o formato usado pela Raposa."""
    url_final, html = _resolver_link(link)
    item_id = _extrair_item_id(url_final, html)

    logger.info("Mercado Livre: consultando item %s", item_id)

    access_token = os.getenv("MERCADO_LIVRE_ACCESS_TOKEN", "").strip()
    api_headers = {
        "Accept": "application/json",
        "User-Agent": "RaposaCacadora/1.0",
    }
    if access_token:
        api_headers["Authorization"] = f"Bearer {access_token}"

    try:
        response = requests.get(
            f"{MERCADO_LIVRE_API_URL}/items/{item_id}",
            timeout=30,
            headers=api_headers,
        )
    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro de conexão com o Mercado Livre: {erro}"
        ) from erro

    if response.status_code == 403:
        logger.warning(
            "Mercado Livre API retornou 403 para %s; usando dados públicos da página.",
            item_id,
        )
        return _extrair_dados_da_pagina(html, url_final, item_id)

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
