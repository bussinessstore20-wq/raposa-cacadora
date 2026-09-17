import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

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
    Cria uma sessão HTTP para requisições na API pública do Mercado Livre.
    Se um token estiver configurado via variável de ambiente, ele será enviado.
    Caso contrário, fará as requisições públicas sem falhar.
    """
    session = requests.Session()
    access_token = os.getenv("MERCADOLIVRE_ACCESS_TOKEN", "").strip()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    session.headers.update(headers)
    return session


# ============================================================
# EXTRAÇÃO DE ITEM ID (MLB)
# ============================================================

def extrair_item_id_da_string(conteudo: str) -> Optional[str]:
    """
    Extrai um Item ID válido do Mercado Livre (formato MLB + 8 a 10 dígitos).
    Evita capturar hashes ou IDs internos inválidos.
    """
    if not conteudo:
        return None

    # Procura estritamente por MLB seguido de 8 a 10 dígitos
    match = re.search(r"MLB[-_]?(\d{8,10})", conteudo, re.IGNORECASE)
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
                    m = re.search(r"MLB[-_]?(\d{8,10})", val, re.IGNORECASE)
                    if m:
                        return f"MLB{m.group(1)}".upper()
    except Exception:
        pass

    return None


def extrair_item_id(valor: str) -> str:
    """Valida e extrai o Item ID de uma string fornecida."""
    if not valor:
        raise MercadoLivreAPIError("Valor vazio para extração do Item ID.")

    item_id = extrair_item_id_da_string(str(valor).strip())
    if not item_id:
        raise MercadoLivreAPIError(f"Item ID inválido ou não encontrado: {valor}")

    return item_id


# ============================================================
# RESOLVER LINK E ACESSAR VITRINE
# ============================================================

def resolver_link_e_extrair_id(link: str) -> Tuple[str, str]:
    """
    Resolve o link de redirecionamento.
    Caso o link caia em uma vitrine/perfil social (/social/), o script varre o HTML
    para encontrar o produto de destino dentro da vitrine.
    """
    link = link.strip()
    if not link:
        raise MercadoLivreAPIError("Link do Mercado Livre está vazio.")

    logger.info("Resolvendo link do Mercado Livre: %s", link)

    session = _obter_sessao()

    try:
        response = session.get(
            link,
            allow_redirects=True,
            timeout=30,
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

    # 1. Tenta extrair um MLB legítimo direto da URL final
    item_id = extrair_item_id_da_string(url_final)
    if item_id:
        logger.info("Item ID extraído diretamente da URL final: %s", item_id)
        return url_final, item_id

    # 2. Se a URL for de uma vitrine (/social/), varre o HTML da vitrine buscando produtos
    if "/social/" in url_final and response.text:
        logger.info("Página de vitrine detectada (/social/). Procurando produtos na vitrine...")

        soup = BeautifulSoup(response.text, "html.parser")

        # Busca 1: Varre todas as tags de link <a> na vitrine
        for tag_a in soup.find_all("a", href=True):
            href = tag_a["href"]
            id_encontrado = extrair_item_id_da_string(href)
            if id_encontrado:
                logger.info("Produto encontrado nos links da vitrine: %s", id_encontrado)
                return url_final, id_encontrado

        # Busca 2: Regex no HTML completo (para scripts, JSON-LD ou dados renderizados)
        matches = re.findall(r"MLB[-_]?(\d{8,10})", response.text, re.IGNORECASE)
        if matches:
            id_encontrado = f"MLB{matches[0]}".upper()
            logger.info("Produto encontrado via Regex no corpo da vitrine: %s", id_encontrado)
            return url_final, id_encontrado

    raise MercadoLivreAPIError(
        "Não foi possível extrair nenhum produto válido do link ou da vitrine informada."
    )


# ============================================================
# CONSULTA DE PRODUTOS VIA API PÚBLICA
# ============================================================

def _api_get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executa uma chamada GET na API do Mercado Livre."""
    url = f"{MERCADOLIVRE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    session = _obter_sessao()

    logger.info("Consultando API do Mercado Livre: %s", url)

    try:
        response = session.get(url, params=params, timeout=30)
    except requests.RequestException as erro:
        raise MercadoLivreAPIError(
            f"Erro de conexão com a API do Mercado Livre: {erro}"
        ) from erro

    logger.info("Mercado Livre HTTP %d", response.status_code)

    if response.status_code != 200:
        logger.error("Resposta do Mercado Livre: %s", response.text[:1000])
        raise MercadoLivreAPIError(
            f"Mercado Livre respondeu HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as erro:
        raise MercadoLivreAPIError(
            "A API do Mercado Livre retornou uma resposta em formato inválido (não JSON)."
        ) from erro


def _consultar_item(item_id: str) -> Dict[str, Any]:
    """Consulta as informações públicas de um anúncio através do seu MLB."""
    item_id = str(item_id).strip().upper()

    if not re.fullmatch(r"MLB\d{8,10}", item_id):
        raise MercadoLivreAPIError(f"Formato de Item ID inválido para API: {item_id}")

    return _api_get(f"/items/{item_id}")


# ============================================================
# BUSCAR E FORMATAR DADOS DO PRODUTO
# ============================================================

def buscar_produto_por_ids(item_id: Any, shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Consulta os detalhes do produto e formata a resposta para o sistema."""
    logger.info("Consultando produto: itemId=%s", item_id)

    item_id_normalizado = extrair_item_id(str(item_id))
    produto_api = _consultar_item(item_id_normalizado)

    if not produto_api:
        logger.warning("Nenhum dado retornado para %s", item_id_normalizado)
        return None

    item_id_retorno = produto_api.get("id") or item_id_normalizado
    seller_id = produto_api.get("seller_id")

    # Extração de imagem principal
    image_url = None
    pictures = produto_api.get("pictures") or []
    if pictures and isinstance(pictures[0], dict):
        image_url = pictures[0].get("secure_url") or pictures[0].get("url")

    # Preços e descontos
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

    logger.info("Produto processado com sucesso: %s", produto.get("productName"))
    return produto


def buscar_produto_por_link(link: str) -> Dict[str, Any]:
    """
    Função principal: Resolve o link encurtado/vitrine, extrai o produto real
    e retorna as informações com o seu link de afiliado original associado.
    """
    if not link:
        raise MercadoLivreAPIError("Link de entrada está vazio.")

    link = link.strip()

    # 1. Resolve o link e localiza o MLB do produto
    url_final, item_id = resolver_link_e_extrair_id(link)

    # 2. Busca dados públicos do produto na API do Mercado Livre
    produto = buscar_produto_por_ids(item_id=item_id)

    if not produto:
        raise MercadoLivreAPIError(
            "O produto não foi encontrado na API do Mercado Livre."
        )

    # 3. Mantém o link de afiliado original para garantir as suas comissões
    produto.update({
        "manualAffiliateLink": link,
        "affiliateLink": link,
        "originalAffiliateLink": link,
        "resolvedProductLink": url_final,
        "itemId": item_id,
    })

    return produto


# ============================================================
# TESTE LOCAL
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    # Teste com a URL encurtada de vitrine
    link_teste = os.getenv("MERCADOLIVRE_LINK_TESTE", "https://meli.la/12w2nSd").strip()

    try:
        resultado = buscar_produto_por_link(link_teste)
        print("\n=== PRODUTO PROCESSADO COM SUCESSO ===")
        print("Nome:", resultado.get("productName"))
        print("Preço:", resultado.get("price"))
        print("Item ID:", resultado.get("itemId"))
        print("Imagem:", resultado.get("imageUrl"))
        print("Link de Afiliado Preservado:", resultado.get("affiliateLink"))
    except MercadoLivreAPIError as erro:
        logger.error("Falha ao processar link: %s", erro)
