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

    html = unescape(unquote(html))
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

    # O título e a imagem vêm primeiro dos metadados públicos da página.
    for pattern in (
        r'<h1[^>]*>(.*?)</h1>',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pattern, html, re.I | re.S)
        if m:
            titulo = limpar(m.group(1))
            if titulo and not titulo.lower().startswith("mercado livre"):
                dados["title"] = titulo
                break

    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"(?:secure_url|original|url|src)"\s*:\s*"(https?[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
        r'(https?:[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?)',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            imagem = unescape(m.group(1)).replace("\\/", "/").strip()
            if imagem.startswith("//"):
                imagem = "https:" + imagem
            dados["image"] = imagem
            break

    moeda_re = r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)"

    # 1) Maior prioridade: relação explícita DE -> POR na parte visível da página.
    pares_visiveis = []
    for m in re.finditer(
        rf"(?:de|era|antes)\s*:?\s*(?:<[^>]+>\s*)*{moeda_re}"
        rf"[^R$]{{0,500}}?"
        rf"(?:por|agora|preço atual|preco atual)\s*:?\s*(?:<[^>]+>\s*)*{moeda_re}",
        html,
        re.I | re.S,
    ):
        original = moeda(m.group(1))
        atual = moeda(m.group(2))
        if original > atual > 0:
            pares_visiveis.append((original, atual))

    # Também procura no texto visível já sem tags, caso o HTML tenha spans
    # entre as palavras e os valores.
    for m in re.finditer(
        rf"(?:de|era|antes)\s*:?\s*{moeda_re}"
        rf".{{0,500}}?"
        rf"(?:por|agora|preço atual|preco atual)\s*:?\s*{moeda_re}",
        texto,
        re.I,
    ):
        original = moeda(m.group(1))
        atual = moeda(m.group(2))
        if original > atual > 0:
            pares_visiveis.append((original, atual))

    if pares_visiveis:
        dados["original_price"], dados["price"] = pares_visiveis[0]

    # 2) Estado estruturado: só aceita pares que estejam próximos e que
    # representem uma redução real. Não usa o primeiro par arbitrário da página.
    if not dados.get("price"):
        candidatos = []
        padroes = (
            r'"original_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,?.{0,500}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,?.{0,500}?"original_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'"previous_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,?.{0,500}?"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,?.{0,500}?"previous_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        )
        for indice, padrao in enumerate(padroes):
            for m in re.finditer(padrao, html, re.I | re.S):
                if indice % 2 == 0:
                    original, atual = float(m.group(1)), float(m.group(2))
                else:
                    atual, original = float(m.group(1)), float(m.group(2))
                if original > atual > 0:
                    candidatos.append((original, atual))
        if candidatos:
            # Prefere o maior preço original entre candidatos de promoção,
            # evitando parcelas pequenas como 3,99.
            dados["original_price"], dados["price"] = max(candidatos, key=lambda par: par[0])

    # 3) Componentes visuais do preço. Ignora explicitamente blocos de
    # parcelamento e usa pares DE/POR quando existirem.
    if not dados.get("price"):
        precos = []
        for m in re.finditer(
            r'class=["\'][^"\']*andes-money-amount__fraction[^"\']*["\'][^>]*>\s*([0-9.]+)\s*<',
            html,
            re.I,
        ):
            trecho = html[max(0, m.start()-700):m.end()+700]
            if re.search(r"parcela|parcelas|mensal|por mês|por mes", trecho, re.I):
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
        if precos:
            dados["price"] = min(precos)

    # 4) Último recurso: preço marcado como "por/agora/oferta".
    if not dados.get("price"):
        for pattern in (
            rf"(?:por|agora|oferta|preço atual|preco atual)\s*:?\s*(?:<[^>]+>\s*)*{moeda_re}",
            rf"(?:por|agora|oferta|preço atual|preco atual)[^R$]{{0,160}}{moeda_re}",
        ):
            m = re.search(pattern, html, re.I | re.S)
            if m:
                dados["price"] = moeda(m.group(1))
                break

    # Vendas: tenta o estado estruturado e depois vários formatos visíveis.
    vendas = 0
    for pattern in (
        r'"sold_quantity"\s*:\s*"?([0-9]+)"?',
        r'"soldQuantity"\s*:\s*"?([0-9]+)"?',
        r'"sold_quantity"\s*:\s*([0-9]+)',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            vendas = int(m.group(1))
            break

    if vendas <= 0:
        padroes_vendas = (
            r"(?:mais\s+de\s+)?([0-9][0-9.\s]*)\s*(?:unidades?\s*)?(?:vendidos?|vendidas?|vendas)",
            r"(?:mais\s+de\s+)?([0-9]+(?:[.,][0-9]+)?)\s*(mil|milhão|milhões|mi|k)\s*(?:unidades?\s*)?(?:vendidos?|vendidas?|vendas)",
            r"([0-9][0-9.]*)\+?\s*(?:vendidos?|vendidas?)",
        )
        for pattern in padroes_vendas:
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
