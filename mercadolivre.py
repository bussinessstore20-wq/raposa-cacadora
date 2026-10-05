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

    import json

    html = unescape(unquote(html)).replace("\\/","/")

    def limpar(valor):
        valor = unescape(str(valor or ""))
        valor = re.sub(r"<[^>]+>", " ", valor)
        return re.sub(r"\s+", " ", valor).strip()

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

    texto = limpar(html)
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
            if titulo and "mercado livre" not in titulo.lower():
                dados["title"] = titulo
                break

    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"(?:secure_url|url)"\s*:\s*"(https?://[^"]+)"',
    ):
        m = re.search(pattern, html, re.I | re.S)
        if m:
            imagem = unescape(m.group(1)).replace("\\/","/").strip()
            if imagem.startswith("http"):
                dados["image"] = imagem
                break

    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
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
            ofertas = offers if isinstance(offers, list) else [offers]
            for offer in ofertas:
                if isinstance(offer, dict):
                    p = moeda(offer.get("price"))
                    if p > 0:
                        jsonld_prices.append(p)

    currency_re = re.compile(
        r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)",
        re.I,
    )
    limite_principal = re.split(
        r"ver\s+meios\s+de\s+pagamento|meios\s+de\s+pagamento|formas\s+de\s+pagamento",
        texto, maxsplit=1, flags=re.I,
    )[0]

    valores = []
    vistos = set()
    for match in currency_re.finditer(limite_principal):
        valor = moeda(match.group(0))
        if valor > 0 and valor not in vistos:
            valores.append(valor)
            vistos.add(valor)

    pares = []
    padroes_par = (
        r"(?:de|era|antes)\s*:?\s*(R\$\s*[0-9][0-9.]*[,\.][0-9]{2}).{0,500}?(?:por|agora|preço\s+atual|preco\s+atual)\s*:?\s*(R\$\s*[0-9][0-9.]*[,\.][0-9]{2})",
        r"(?:por|agora|preço\s+atual|preco\s+atual)\s*:?\s*(R\$\s*[0-9][0-9.]*[,\.][0-9]{2}).{0,500}?(?:de|era|antes)\s*:?\s*(R\$\s*[0-9][0-9.]*[,\.][0-9]{2})",
    )
    for indice, pattern in enumerate(padroes_par):
        m = re.search(pattern, limite_principal, re.I | re.S)
        if m:
            if indice == 0:
                original, atual = moeda(m.group(1)), moeda(m.group(2))
            else:
                atual, original = moeda(m.group(1)), moeda(m.group(2))
            if original > atual > 0:
                pares.append((original, atual))

    if pares:
        dados["original_price"], dados["price"] = pares[0]
    elif len(valores) >= 2 and valores[0] > valores[1] > 0:
        dados["original_price"], dados["price"] = valores[0], valores[1]
    elif valores:
        dados["price"] = valores[0]

    if not dados.get("price") and jsonld_prices:
        dados["price"] = jsonld_prices[0]

    def converter_quantidade(numero_texto, unidade=""):
        s = str(numero_texto or "").replace(".", "").replace(",", ".").replace(" ", "")
        try:
            valor = float(s)
        except (TypeError, ValueError):
            return 0
        unidade = str(unidade or "").lower()
        if unidade in ("mil", "k"):
            valor *= 1000
        elif unidade in ("milhão", "milhões", "mi"):
            valor *= 1000000
        return int(valor)

    vendas = 0
    for pattern in (
        r"\+?\s*([0-9][0-9.,\s]*)\s*(milhão|milhões|mil|mi|k)?\s+(?:unidades?\s+)?vendidos?",
        r"\+?\s*([0-9][0-9.,\s]*)\s*(milhão|milhões|mil|mi|k)?\s+(?:unidades?\s+)?vendidas?",
        r"\+?\s*([0-9][0-9.,\s]*)\s*(milhão|milhões|mil|mi|k)?\s+vendas",
    ):
        m = re.search(pattern, texto, re.I)
        if m:
            vendas = converter_quantidade(m.group(1), m.group(2))
            if vendas > 0:
                break

    if vendas <= 0:
        for pattern in (
            r'"sold_quantity"\s*:\s*"?([0-9]+)"?',
            r'"soldQuantity"\s*:\s*"?([0-9]+)"?',
            r'"sold"\s*:\s*"?([0-9]+)"?',
        ):
            m = re.search(pattern, html, re.I)
            if m:
                vendas = int(m.group(1))
                break

    preco = moeda(dados.get("price"))
    original = moeda(dados.get("original_price"))
    if original <= preco or preco <= 0:
        original = 0.0

    desconto = ((original - preco) / original * 100) if original > preco > 0 else 0.0
    titulo = limpar(dados.get("title")) or f"Produto Mercado Livre {item_id}"
    imagem = limpar(dados.get("image"))

    if preco <= 0:
        raise MercadoLivreAPIError(f"Não foi possível identificar o preço atual do anúncio {item_id}.")

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


def _consultar_cupom_item(access_token: str, item_id: str) -> dict:
    """
    Consulta uma campanha de cupom do vendedor vinculada ao item.

    Cupom é enriquecimento opcional: falhas de permissão/API nunca impedem
    a coleta e publicação normal do produto.
    """
    resultado = {}
    if not access_token or not item_id:
        return resultado

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "RaposaCacadora/1.0",
    }

    try:
        response = requests.get(
            f"{MERCADO_LIVRE_API_URL}/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            timeout=15,
            headers=headers,
        )
    except requests.RequestException as erro:
        logger.warning("Mercado Livre: falha ao consultar promoções de %s: %s", item_id, erro)
        return resultado

    if response.status_code in (401, 403):
        logger.warning(
            "Mercado Livre: cupons indisponíveis para %s (HTTP %s); "
            "produto seguirá normalmente.",
            item_id,
            response.status_code,
        )
        return resultado

    if response.status_code >= 400:
        logger.warning(
            "Mercado Livre: HTTP %s ao consultar promoções de %s.",
            response.status_code,
            item_id,
        )
        return resultado

    try:
        promocoes = response.json()
    except ValueError:
        logger.warning("Mercado Livre: resposta inválida de promoções para %s.", item_id)
        return resultado

    if isinstance(promocoes, dict):
        promocoes = promocoes.get("results") or promocoes.get("promotions") or []
    if not isinstance(promocoes, list):
        promocoes = []

    candidatas = [
        p for p in promocoes
        if isinstance(p, dict)
        and str(p.get("type") or "").upper() == "SELLER_COUPON_CAMPAIGN"
        and str(p.get("status") or "").lower() in {"started", "active"}
    ]

    for promocao in candidatas:
        promotion_id = (
            promocao.get("id")
            or promocao.get("promotion_id")
            or promocao.get("promotionId")
        )
        detalhe = dict(promocao)

        if promotion_id:
            try:
                detalhe_response = requests.get(
                    f"{MERCADO_LIVRE_API_URL}/seller-promotions/promotions/{promotion_id}",
                    params={
                        "promotion_type": "SELLER_COUPON_CAMPAIGN",
                        "app_version": "v2",
                    },
                    timeout=15,
                    headers=headers,
                )
                if detalhe_response.status_code < 400:
                    corpo = detalhe_response.json()
                    if isinstance(corpo, dict):
                        detalhe.update(corpo)
            except (requests.RequestException, ValueError):
                logger.info(
                    "Mercado Livre: não foi possível obter detalhe do cupom %s.",
                    promotion_id,
                )

        status = str(detalhe.get("status") or promocao.get("status") or "").lower()
        if status not in {"started", "active"}:
            continue

        coupon_code = str(detalhe.get("coupon_code") or "").strip()
        subtype = str(
            detalhe.get("sub_type") or promocao.get("sub_type") or ""
        ).upper()

        fixed_amount = detalhe.get("fixed_amount")
        if fixed_amount is None:
            fixed_amount = promocao.get("fixed_amount")

        fixed_percentage = detalhe.get("fixed_percentage")
        if fixed_percentage is None:
            fixed_percentage = promocao.get("fixed_percentage")

        min_purchase = detalhe.get("min_purchase_amount")
        if min_purchase is None:
            min_purchase = promocao.get("min_purchase_amount")

        max_refund = detalhe.get("max_purchase_amount")
        if max_refund is None:
            max_refund = promocao.get("max_purchase_amount")

        resultado = {
            "couponActive": True,
            "couponPromotionId": str(promotion_id or detalhe.get("id") or ""),
            "couponName": str(detalhe.get("name") or ""),
            "couponCode": coupon_code,
            "couponSubtype": subtype,
            "couponDiscountAmount": float(fixed_amount or 0),
            "couponDiscountPercentage": float(fixed_percentage or 0),
            "couponMinPurchaseAmount": float(min_purchase or 0),
            "couponMaxRefund": float(max_refund or 0),
            "couponStartDate": detalhe.get("start_date") or promocao.get("start_date"),
            "couponFinishDate": detalhe.get("finish_date") or promocao.get("finish_date"),
            "couponRemainingBudget": float(detalhe.get("remaining_budget") or 0),
            "couponUsedCoupons": int(detalhe.get("used_coupons") or 0),
        }

        logger.info(
            "Cupom Mercado Livre encontrado: item=%s | promoção=%s | tipo=%s | "
            "valor=%s | percentual=%s | código=%s",
            item_id,
            resultado["couponPromotionId"],
            subtype,
            resultado["couponDiscountAmount"],
            resultado["couponDiscountPercentage"],
            "sim" if coupon_code else "não",
        )
        return resultado

    return resultado

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
        produto = _extrair_dados_da_pagina(html, url_final, item_id)
        try:
            produto.update(_consultar_cupom_item(access_token, produto["itemId"]))
        except Exception:
            logger.exception(
                "Mercado Livre: falha inesperada ao enriquecer %s com cupom; "
                "publicação continuará normalmente.",
                produto["itemId"],
            )
        return produto

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

    try:
        produto.update(_consultar_cupom_item(access_token, produto["itemId"]))
    except Exception:
        logger.exception(
            "Mercado Livre: falha inesperada ao enriquecer %s com cupom; "
            "publicação continuará normalmente.",
            produto["itemId"],
        )

    logger.info(
        "Produto Mercado Livre encontrado: %s | item=%s",
        produto["productName"],
        produto["itemId"],
    )

    return produto
