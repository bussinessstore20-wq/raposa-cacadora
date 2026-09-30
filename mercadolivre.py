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

    html_decodificado = unescape(unquote(html))
    dados = {}

    def _limpar_html(valor):
        return re.sub(
            r"\\s+",
            " ",
            re.sub(r"<[^>]+>", " ", unescape(str(valor or ""))),
        ).strip()

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

    def _numero_bruto(valor):
        try:
            return float(str(valor).replace(".", "").replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    # Metadados da própria página: mais confiáveis para título e imagem.
    for padrao in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE)
        if match and not dados.get("title"):
            dados["title"] = _limpar_html(match.group(1))

    for padrao in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE)
        if match and not dados.get("image"):
            dados["image"] = unescape(match.group(1)).strip()

    match = re.search(
        r'<h1[^>]*>(.*?)</h1>',
        html_decodificado,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        dados["title"] = _limpar_html(match.group(1))

    # Dados estruturados do anúncio. Não usamos JSON-LD para preço, porque
    # ele pode conservar um preço antigo enquanto a página mostra uma oferta.
    campos = {
        "title": (
            r'"(?:title|name)"\\s*:\\s*"([^"]{5,300})"',
        ),
        "image": (
            r'"(?:secure_url|thumbnail|image)"\\s*:\\s*"(https?:[^"]+)"',
        ),
    }
    for campo, padroes in campos.items():
        if dados.get(campo):
            continue
        for padrao in padroes:
            match = re.search(padrao, html_decodificado, re.IGNORECASE)
            if match:
                dados[campo] = unescape(match.group(1))
                break

    # Captura pares price/original_price do mesmo bloco de dados.
    pares = []
    for match in re.finditer(
        r'"original_price"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)'
        r'.{0,1200}?'
        r'"price"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)',
        html_decodificado,
        re.IGNORECASE | re.DOTALL,
    ):
        original = float(match.group(1))
        atual = float(match.group(2))
        if original > atual > 0:
            pares.append((original, atual))

    for match in re.finditer(
        r'"price"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)'
        r'.{0,1200}?'
        r'"original_price"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)',
        html_decodificado,
        re.IGNORECASE | re.DOTALL,
    ):
        atual = float(match.group(1))
        original = float(match.group(2))
        if original > atual > 0:
            pares.append((original, atual))

    if pares:
        # Preferimos o par economicamente consistente do anúncio.
        original, atual = pares[0]
        dados["original_price"] = original
        dados["price"] = atual

    # Fallback para texto visível explicitamente associado a "de" e "por".
    moeda_re = r"R\\$\\s*([0-9]{1,3}(?:\\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\\.[0-9]{2})?)"

    if not dados.get("original_price"):
        for padrao in (
            rf"(?:de|era|antes)\\s*:?\\s*(?:<[^>]+>\\s*)*{moeda_re}",
            rf"(?:de|era|antes)[^R$]{{0,100}}{moeda_re}",
        ):
            match = re.search(padrao, html_decodificado, re.IGNORECASE | re.DOTALL)
            if match:
                dados["original_price"] = match.group(1)
                break

    if not dados.get("price"):
        for padrao in (
            rf"(?:por|agora|oferta|preço atual|preco atual)\\s*:?\\s*(?:<[^>]+>\\s*)*{moeda_re}",
            rf"(?:por|agora|oferta|preço atual|preco atual)[^R$]{{0,100}}{moeda_re}",
        ):
            match = re.search(padrao, html_decodificado, re.IGNORECASE | re.DOTALL)
            if match:
                dados["price"] = match.group(1)
                break

    # Como último recurso, usa o preço em classes monetárias. Não usa min/max,
    # pois isso misturava preço de variantes, frete ou componentes da página.
    if not dados.get("price"):
        visiveis = []
        for match in re.finditer(
            r'class=["\'][^"\']*andes-money-amount__fraction[^"\']*["\'][^>]*>\\s*([0-9.]+)\\s*<',
            html_decodificado,
            re.IGNORECASE,
        ):
            trecho = html_decodificado[max(0, match.start() - 350):match.end() + 350]
            cents = re.search(
                r'class=["\'][^"\']*andes-money-amount__cents[^"\']*["\'][^>]*>\\s*([0-9]{1,2})\\s*<',
                trecho,
                re.IGNORECASE,
            )
            cent = cents.group(1) if cents else "00"
            try:
                visiveis.append(float(match.group(1).replace(".", "") + "." + cent.zfill(2)))
            except ValueError:
                pass
        if visiveis:
            dados["price"] = visiveis[0]

    # Vendas: sold_quantity do estado interno ou texto "X vendidos".
    vendas = 0
    for padrao in (
        r'"(?:sold_quantity|soldQuantity)"\\s*:\\s*([0-9]+)',
        r'"(?:sold_quantity|soldQuantity)"\\s*:\\s*"([0-9]+)"',
    ):
        match = re.search(padrao, html_decodificado, re.IGNORECASE)
        if match:
            vendas = int(match.group(1))
            break

    if vendas <= 0:
        for padrao in (
            r'(?:mais\\s+de\\s*)?([0-9][0-9.\\s]*)\\s*(?:unidades?\\s*)?(?:vendid[oa]s?|vendas)',
            r'(?:mais\\s+de\\s*)?([0-9]+(?:[.,][0-9]+)?)\\s*(mil(?:hão|hões)?|mi|k)\\s*(?:unidades?\\s*)?(?:vendid[oa]s?|vendas)',
        ):
            match = re.search(padrao, html_decodificado, re.IGNORECASE)
            if not match:
                continue
            try:
                texto = match.group(1).replace(".", "").replace(" ", "").replace(",", ".")
                vendas = int(float(texto))
                if match.lastindex and match.lastindex >= 2:
                    unidade = match.group(2).lower()
                    if unidade in {"mil", "k"}:
                        vendas *= 1000
                    elif unidade in {"milhão", "milhões", "mi"}:
                        vendas *= 1000000
                break
            except (ValueError, TypeError):
                pass

    preco = _float_moeda(dados.get("price"))
    preco_original = _float_moeda(dados.get("original_price"))

    if preco_original <= preco or preco <= 0:
        preco_original = 0.0

    desconto = (
        ((preco_original - preco) / preco_original) * 100
        if preco_original > preco > 0
        else 0.0
    )

    titulo = _limpar_html(dados.get("title")) or f"Produto Mercado Livre {item_id}"
    imagem = _limpar_html(dados.get("image"))

    produto = {
        "productName": titulo,
        "itemId": item_id,
        "shopId": None,
        "price": preco,
        "priceMin": preco,
        "priceMax": preco,
        "originalPrice": preco_original,
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

    if not produto["productName"] and not produto["imageUrl"] and not produto["price"]:
        raise MercadoLivreAPIError(
            f"Mercado Livre bloqueou a API para {item_id} e a página não expôs dados do produto."
        )

    logger.info(
        "Mercado Livre fallback: item=%s | título=%s | preço=%s | original=%s | vendas=%s | imagem=%s",
        item_id,
        titulo,
        preco,
        preco_original,
        vendas,
        bool(imagem),
    )
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
