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
        raise MercadoLivreAPIError(
            f"Mercado Livre bloqueou a API para {item_id} e não foi possível ler a página."
        )

    import json

    html = unescape(unquote(html)).replace("\\/","/")
    texto = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

    def moeda(valor):
        try:
            s = str(valor or "").replace("R$", "").replace(" ", "").strip()
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            return float(s)
        except (TypeError, ValueError):
            return 0.0

    def limpar(valor):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(valor or "")))).strip()

    dados = {}
    jsonld_prices = []

    for pattern in (
        r'<h1[^>]*>(.*?)</h1>',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pattern, html, re.I | re.S)
        if m:
            titulo = limpar(m.group(1))
            if titulo and not titulo.lower().startswith(("mercado livre", "produto mercado livre")):
                dados["title"] = titulo
                break

    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            dados["image"] = unescape(m.group(1)).replace("\\/","/").strip()
            break

    # JSON-LD: usado principalmente para título/imagem e somente como
    # último recurso para preço.
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        try:
            obj = json.loads(unescape(m.group(1).strip()))
        except Exception:
            continue
        objetos = obj if isinstance(obj, list) else [obj]
        for item in objetos:
            if not isinstance(item, dict):
                continue
            if not dados.get("title") and item.get("name"):
                dados["title"] = limpar(item.get("name"))
            if not dados.get("image") and item.get("image"):
                imagem = item.get("image")
                if isinstance(imagem, list):
                    imagem = imagem[0] if imagem else ""
                dados["image"] = str(imagem or "").replace("\\/","/").strip()
            offers = item.get("offers")
            if isinstance(offers, dict):
                p = moeda(offers.get("price"))
                if p > 0:
                    jsonld_prices.append(p)
            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        p = moeda(offer.get("price"))
                        if p > 0:
                            jsonld_prices.append(p)

    moeda_re = r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)"

    # Prioridade máxima: par explícito DE/POR.
    pares = []
    padroes = (
        rf"(?:de|era|antes)\s*:?\s*{moeda_re}[^R$]{{0,900}}?(?:por|agora|preço atual|preco atual)\s*:?\s*{moeda_re}",
        rf"(?:por|agora|preço atual|preco atual)\s*:?\s*{moeda_re}[^R$]{{0,900}}?(?:de|era|antes)\s*:?\s*{moeda_re}",
    )
    for indice, pattern in enumerate(padroes):
        for m in re.finditer(pattern, texto, re.I):
            original, atual = (moeda(m.group(1)), moeda(m.group(2))) if indice == 0 else (moeda(m.group(2)), moeda(m.group(1)))
            if original > atual > 0:
                pares.append((original, atual))

    if not pares:
        for indice, pattern in enumerate(padroes):
            for m in re.finditer(pattern, html, re.I | re.S):
                original, atual = (moeda(m.group(1)), moeda(m.group(2))) if indice == 0 else (moeda(m.group(2)), moeda(m.group(1)))
                if original > atual > 0:
                    pares.append((original, atual))

    if pares:
        original, atual = max(pares, key=lambda par: (par[0] - par[1], par[0]))
        dados["original_price"] = original
        dados["price"] = atual

    # Segundo nível: preço explicitamente rotulado como atual/oferta.
    if not dados.get("price"):
        for pattern in (
            rf"(?:por|agora|preço atual|preco atual|oferta)\s*:?\s*{moeda_re}",
            rf"(?:por|agora|preço atual|preco atual|oferta)[^R$]{{0,220}}{moeda_re}",
        ):
            m = re.search(pattern, texto, re.I)
            if m:
                valor = moeda(m.group(1))
                if valor > 0:
                    dados["price"] = valor
                    break

    # Terceiro nível: preço visual isolado, excluindo parcelas.
    if not dados.get("price"):
        precos = []
        for m in re.finditer(
            r'class=["\'][^"\']*andes-money-amount__fraction[^"\']*["\'][^>]*>\s*([0-9.]+)\s*<',
            html,
            re.I,
        ):
            trecho = html[max(0, m.start()-1000):m.end()+1000]
            if re.search(r"parcela|parcelas|mensal|por mês|por mes|cartão|cartao", trecho, re.I):
                continue
            cents = re.search(
                r'class=["\'][^"\']*andes-money-amount__cents[^"\']*["\'][^>]*>\s*([0-9]{1,2})\s*<',
                trecho,
                re.I,
            )
            cent = cents.group(1) if cents else "00"
            try:
                precos.append(float(m.group(1).replace(".", "") + "." + cent.zfill(2)))
            except ValueError:
                pass
        if len(precos) == 1:
            dados["price"] = precos[0]

    if not dados.get("price") and jsonld_prices:
        dados["price"] = jsonld_prices[0]

    # Vendas: estruturado + texto visível.
    vendas = 0
    for pattern in (
        r'"sold_quantity"\s*:\s*"?(\d+)"?',
        r'"soldQuantity"\s*:\s*"?(\d+)"?',
        r'"sold"\s*:\s*"?(\d+)"?',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            vendas = int(m.group(1))
            break

    if vendas <= 0:
        for pattern in (
            r"(?:mais\s+de\s+)?(\d+(?:[.,]\d+)?)\s*(milhão|milhões|mil|mi|k)\s*(?:unidades?\s*)?(?:vendidos?|vendidas?|vendas)",
            r"(?:mais\s+de\s+)?(\d[\d.\s]*)\+?\s*(?:unidades?\s*)?(?:vendidos?|vendidas?|vendas)",
        ):
            m = re.search(pattern, texto, re.I)
            if not m:
                continue
            try:
                valor = float(m.group(1).replace(".", "").replace(" ", "").replace(",", "."))
                unidade = (m.group(2) or "").lower() if m.lastindex and m.lastindex >= 2 else ""
                if unidade in ("mil", "k"):
                    valor *= 1000
                elif unidade in ("milhão", "milhões", "mi"):
                    valor *= 1000000
                vendas = int(valor)
                break
            except (TypeError, ValueError):
                pass

    preco = moeda(dados.get("price"))
    original = moeda(dados.get("original_price"))
    if original <= preco or preco <= 0:
        original = 0.0

    desconto = ((original - preco) / original * 100) if original > preco > 0 else 0.0
    titulo = limpar(dados.get("title")) or f"Produto Mercado Livre {item_id}"
    imagem = limpar(dados.get("image"))

    return {
        "productName": titulo,
        "itemId": item_id,
        "shopId": None,
        "price": preco,
        "priceMin": preco,
        "priceMax": preco,
        "originalPrice": original,
        "priceDiscountRate": desconto,
        "ratingStar": 0,
        "sales": vendas,
        "shopName": "Mercado Livre",
        "imageUrl": imagem,
        "productLink": url_final,
        "offerLink": url_final,
        "manualAffiliateLink": url_final,
        "affiliateLink": url_final,
        "marketplace": "mercadolivre",
    }

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
