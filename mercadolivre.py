import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

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
    "Chrome/130.0.0.0 Safari/537.36"
)


# ============================================================
# ERRO DA API
# ============================================================

class MercadoLivreAPIError(Exception):
    """Erro relacionado à API do Mercado Livre."""


# ============================================================
# SESSÃO E REQUISIÇÕES
# ============================================================

def _obter_sessao() -> requests.Session:
    """
    Cria uma sessão HTTP para requisições na API pública.
    Caso exista um token de acesso configurado, ele será utilizado,
    mas a chamada funcionará normalmente sem ele.
    """
    session = requests.Session()
    access_token = os.getenv("MERCADOLIVRE_ACCESS_TOKEN", "").strip()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }

    # Adiciona o Authorization apenas se o token for fornecido
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    session.headers.update(headers)
    return session


# ============================================================
# RESOLVER LINK E EXTRAIR ITEM ID
# ============================================================

def extrair_item_id_da_string(conteudo: str) -> Optional[str]:
    """Auxiliar para extrair o formato MLB12345678 de qualquer texto/URL."""
    if not conteudo:
        return None

    # Procura por MLB1234567890 ou MLB-1234567890
    match = re.search(r"MLB[-_]?(\d{6,})", conteudo, re.IGNORECASE)
    if match:
        return f"MLB{match.group(1)}".upper()

    # Busca em parâmetros query se for uma URL
    try:
        parsed = urlparse(conteudo)
        if parsed.query:
            params = parse_qs(parsed.query)
            for key in ("item_id", "itemId", "itemid"):
                if key in params:
                    val = params[key][0]
                    m = re.search(r"MLB[-_]?(\d{6,})", val, re.IGNORECASE)
                    if m:
                        return f"MLB{m.group(1)}".upper()
    except Exception:
        pass

    return None


def extrair_item_id(valor: str) -> str:
    """Extrai Item ID diretamente do valor fornecido."""
    if not valor:
        raise MercadoLivreAPIError("Valor vazio para extração do Item ID.")

    item_id = extrair_item_id_da_string(str(valor).strip())
    if not item_id:
        raise MercadoLivreAPIError(f"Item ID inválido: {valor}")

    return item_id


def resolver_link_e_extrair_id(link: str) -> Tuple[str, str]:
    """
    Resolve o link redirecionado e tenta extrair o Item ID (MLB)
    seja pela URL final ou inspecionando o corpo do HTML.
    """
    link = link.strip()
    if not link:
        raise MercadoLivreAPIError("Link do Mercado Livre está vazio.")

    logger.info("Resolvendo link do Mercado Livre: %s", link)

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
    }

    try:
        response = requests.get(
            link,
            allow_redirects=True,
            timeout=30,
            headers=headers,
        )
        logger.info("HTTP ao resolver link: %d", response.status_code)

        if response.status_code >= 400:
            raise MercadoLivreAPIError(
                f"Mercado Livre retornou HTTP {response.status_code} ao resolver o link."
            )

    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro ao resolver link do Mercado Livre: {erro}"
        ) from erro

    url_final = response.url
    logger.info("URL final resolvida: %s", url_final)

    if not url_final:
        raise MercadoLivreAPIError("O Mercado Livre não retornou uma URL final.")

    # 1. Tenta extrair pela URL final
    item_id = extrair_item_id_da_string(url_final)

    # 2. Fallback: Se não encontrou na URL, busca no corpo HTML da página
    if not item_id and response.text:
        logger.info("Tentando extrair Item ID do corpo HTML da página...")
        item_id = extrair_item_id_da_string(response.text)

    if not item_id:
        if "/social/" in url_final:
            raise MercadoLivreAPIError(
                "O link enviado pertence a uma página/perfil social de afiliado e não contém um produto identificável."
            )
        raise MercadoLivreAPIError(
            "Não foi possível encontrar o Item ID (MLB) do Mercado Livre na URL ou no HTML."
        )

    logger.info("Item ID encontrado com sucesso: %s", item_id)
    return url_final, item_id


# ============================================================
# CONSULTAS À API PÚBLICA
# ============================================================

def _api_get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executa uma requisição GET na API do Mercado Livre."""
    url = f"{MERCADOLIVRE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    session = _obter_sessao()

    logger.info("Consultando Mercado Livre (Público): %s", url)

    try:
        response = session.get(url, params=params, timeout=30)
    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro de conexão com a API do Mercado Livre: {erro}"
        ) from erro

    logger.info("Mercado Livre HTTP %d", response.status_code)

    if response.status_code != 200:
        logger.error("Resposta do Mercado Livre: %s", response.text[:2000])
        raise MercadoLivreAPIError(
            f"Mercado Livre respondeu HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        return response.json()
    except ValueError as erro:
        raise MercadoLivreAPIError(
            "O Mercado Livre retornou uma resposta inválida (não JSON)."
        ) from erro


def _consultar_item(item_id: str) -> Dict[str, Any]:
    """Busca os detalhes públicos de um anúncio."""
    item_id = str(item_id).strip().upper()

    if not re.fullmatch(r"MLB\d{6,}", item_id):
        raise MercadoLivreAPIError(f"Item ID inválido: {item_id}")

    return _api_get(f"/items/{item_id}")


# ============================================================
# BUSCAR PRODUTO
# ============================================================

def buscar_produto_por_ids(item_id: Any, shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Consulta dados do produto via API pública e formata a resposta."""
    logger.info("Consultando produto: itemId=%s", item_id)

    item_id_normalizado = extrair_item_id(str(item_id))
    produto_api = _consultar_item(item_id_normalizado)

    if not produto_api:
        logger.warning("Nenhum produto retornado para %s", item_id_normalizado)
        return None

    item_id_retorno = produto_api.get("id") or item_id_normalizado
    seller_id = produto_api.get("seller_id")

    # Extração de imagem principal
    image_url = None
    pictures = produto_api.get("pictures") or []
    if pictures and isinstance(pictures[0], dict):
        image_url = pictures[0].get("secure_url") or pictures[0].get("url")

    # Cálculo de preços e descontos
    price = produto_api.get("price")
    original_price = produto_api.get("original_price")
    price_discount_rate = None

    if original_price and price and original_price > 0 and price < original_price:
        price_discount_rate = round(((original_price - price) / original_price) * 100, 2)

    sales = produto_api.get("sold_quantity")
    product_link = produto_api.get("permalink")

    seller_reputation = produto_api.get("seller_reputation") or {}
    rating_star = seller_reputation.get("seller_reputation_level") if isinstance(seller_reputation, dict) else None

    produto = {
        "itemId": item_id_retorno,
        "shopId": shop_id or seller_id,
        "sellerId": seller_id,
        "productName": produto_api.get("title") or "Produto",
        "title": produto_api.get("title") or "Produto",
        "price": price,
        "priceMin": price,
        "priceMax": price,
        "originalPrice": original_price,
        "priceDiscountRate": price_discount_rate,
        "currencyId": produto_api.get("currency_id"),
        "sales": sales,
        "soldQuantity": sales,
        "imageUrl": image_url,
        "thumbnail": produto_api.get("thumbnail"),
        "productLink": product_link,
        "permalink": product_link,
        "shopName": None,
        "shopType": None,
        "ratingStar": rating_star,
        "categoryId": produto_api.get("category_id"),
        "condition": produto_api.get("condition"),
        "availableQuantity": produto_api.get("available_quantity"),
        "listingTypeId": produto_api.get("listing_type_id"),
        "buyingMode": produto_api.get("buying_mode"),
        "siteId": produto_api.get("site_id"),
        "mercadolivreData": produto_api,
    }

    logger.info("Produto encontrado com sucesso: %s", produto.get("productName"))
    return produto


def buscar_produto_por_link(link: str) -> Dict[str, Any]:
    """Resolve o link de afiliado, identifica o MLB e busca as informações do produto."""
    if not link:
        raise MercadoLivreAPIError("Link está vazio.")

    link = link.strip()

    # 1. Resolve redirecionamentos e captura o ID do produto
    url_final, item_id = resolver_link_e_extrair_id(link)

    # 2. Busca dados via API pública
    produto = buscar_produto_por_ids(item_id=item_id)

    if not produto:
        raise MercadoLivreAPIError(
            "O produto não foi encontrado na API do Mercado Livre."
        )

    # Preserva metadados e o link de afiliado original
    produto.update({
        "manualAffiliateLink": link,
        "affiliateLink": link,
        "originalAffiliateLink": link,
        "resolvedProductLink": url_final,
        "itemId": item_id,
    })

    return produto


# ============================================================
# EXECUÇÃO DE TESTE LOCAL
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    link_teste = os.getenv("MERCADOLIVRE_LINK_TESTE", "https://meli.la/12w2nSd").strip()

    try:
        resultado = buscar_produto_por_link(link_teste)
        print("\n=== SUCESSO AO PROCESSAR PRODUTO ===")
        print("Nome:", resultado.get("productName"))
        print("Preço:", resultado.get("price"))
        print("Item ID:", resultado.get("itemId"))
        print("Imagem:", resultado.get("imageUrl"))
        print("Link de Afiliado:", resultado.get("affiliateLink"))
    except MercadoLivreAPIError as erro:
        logger.error("Erro no processamento: %s", erro)
