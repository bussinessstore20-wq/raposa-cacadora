import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

HTTP_TIMEOUT = 30

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


# ============================================================
# ERRO
# ============================================================

class MercadoLivreAPIError(Exception):
    pass


# ============================================================
# SESSÃO WEB
# ============================================================

def _obter_sessao_web() -> requests.Session:

    session = requests.Session()

    session.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,

        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "*/*;q=0.8"
        ),

        "Accept-Language": (
            "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        ),

        "Accept-Encoding": (
            "gzip, deflate"
        ),

        "Cache-Control": "no-cache",

        "Pragma": "no-cache",

        "Upgrade-Insecure-Requests": "1",
    })

    return session


# ============================================================
# EXTRAIR MLB
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:

    if not conteudo:
        return None

    match = re.search(
        r"MLB[-_]?(\d{8,12})",
        str(conteudo),
        re.IGNORECASE,
    )

    if match:

        return (
            f"MLB{match.group(1)}"
            .upper()
        )

    return None


# ============================================================
# NORMALIZAR URL
# ============================================================

def _normalizar_url(
    base_url: str,
    href: str,
) -> Optional[str]:

    if not href:
        return None

    href = (
        str(href)
        .strip()
    )

    if not href:
        return None

    if href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        )
    ):
        return None

    return urljoin(
        base_url,
        href,
    )


# ============================================================
# VERIFICAR SE É LINK DO MERCADO LIVRE
# ============================================================

def _eh_link_mercadolivre(
    url: str,
) -> bool:

    if not url:
        return False

    try:

        host = (
            urlparse(url)
            .netloc
            .lower()
        )

    except Exception:

        return False

    return (
        "mercadolivre.com.br" in host
        or "mercadolibre.com" in host
    )


# ============================================================
# VERIFICAR SE PARECE LINK DE PRODUTO
# ============================================================

def _parece_link_de_produto(
    url: str,
) -> bool:

    if not url:
        return False

    url_lower = (
        url
        .lower()
    )

    # --------------------------------------------------------
    # MLB explícito
    # --------------------------------------------------------

    if extrair_item_id_da_string(
        url
    ):

        return True

    # --------------------------------------------------------
    # Caminhos comuns de produto
    # --------------------------------------------------------

    padroes = (
        "/produto",
        "/p/",
        "/mlb-",
        "/mlb_",
        "/oferta/",
        "/item/",
    )

    if any(
        padrao in url_lower
        for padrao in padroes
    ):

        return True

    return False


# ============================================================
# EXTRAIR LINKS DOS ELEMENTOS HTML
# ============================================================

def _extrair_links_html(
    soup: BeautifulSoup,
    base_url: str,
) -> List[str]:

    encontrados = []

    vistos = set()

    # --------------------------------------------------------
    # <a href="">
    # --------------------------------------------------------

    for tag in soup.find_all(
        "a",
        href=True,
    ):

        href = tag.get(
            "href"
        )

        url = _normalizar_url(
            base_url,
            href,
        )

        if not url:
            continue

        if url in vistos:
            continue

        vistos.add(url)

        if not _eh_link_mercadolivre(
            url
        ):
            continue

        encontrados.append(
            url
        )

    # --------------------------------------------------------
    # atributos data-href / data-url
    # --------------------------------------------------------

    for tag in soup.find_all():

        for atributo in (
            "data-href",
            "data-url",
            "data-link",
            "data-product-url",
        ):

            valor = tag.get(
                atributo
            )

            if not valor:
                continue

            url = _normalizar_url(
                base_url,
                valor,
            )

            if not url:
                continue

            if url in vistos:
                continue

            vistos.add(url)

            if not _eh_link_mercadolivre(
                url
            ):
                continue

            encontrados.append(
                url
            )

    return encontrados


# ============================================================
# EXTRAIR URLS DE JSON EMBUTIDO
# ============================================================

def _extrair_urls_do_json(
    soup: BeautifulSoup,
    base_url: str,
) -> List[str]:

    urls = []

    vistos = set()

    # --------------------------------------------------------
    # Scripts
    # --------------------------------------------------------

    for script in soup.find_all(
        "script"
    ):

        texto = (
            script.string
            or script.get_text()
            or ""
        )

        if not texto:
            continue

        # ----------------------------------------------------
        # URLs absolutas
        # ----------------------------------------------------

        encontrados = re.findall(
            r'https?://[^"\']+',
            texto,
        )

        for url in encontrados:

            url = (
                url
                .replace(
                    "\\/",
                    "/",
                )
                .replace(
                    "\\u002F",
                    "/",
                )
            )

            # Limpeza básica
            url = url.rstrip(
                "\"',}"
            )

            if not _eh_link_mercadolivre(
                url
            ):
                continue

            if url in vistos:
                continue

            vistos.add(url)

            urls.append(
                url
            )

        # ----------------------------------------------------
        # URLs relativas contendo MLB
        # ----------------------------------------------------

        if "MLB" in texto.upper():

            matches = re.findall(
                r'["\']([^"\']*MLB[-_]?\d{8,12}[^"\']*)["\']',
                texto,
                re.IGNORECASE,
            )

            for match in matches:

                url = _normalizar_url(
                    base_url,
                    match,
                )

                if not url:
                    continue

                if url in vistos:
                    continue

                vistos.add(url)

                if _eh_link_mercadolivre(
                    url
                ):

                    urls.append(
                        url
                    )

    return urls


# ============================================================
# ESCOLHER LINK DO PRODUTO
# ============================================================

def _encontrar_link_produto(
    soup: BeautifulSoup,
    base_url: str,
) -> Optional[str]:

    logger.info(
        "Procurando botão/link para o produto..."
    )

    links_html = _extrair_links_html(
        soup,
        base_url,
    )

    links_json = _extrair_urls_do_json(
        soup,
        base_url,
    )

    todos = []

    vistos = set()

    for url in (
        links_html
        + links_json
    ):

        if url in vistos:
            continue

        vistos.add(url)

        todos.append(
            url
        )

    logger.info(
        "Total de links candidatos encontrados: %d",
        len(todos),
    )

    # ========================================================
    # PRIORIDADE 1
    # Link que contém MLB
    # ========================================================

    for url in todos:

        if extrair_item_id_da_string(
            url
        ):

            logger.info(
                "Link de produto encontrado pelo MLB: %s",
                url,
            )

            return url

    # ========================================================
    # PRIORIDADE 2
    # URL com padrão de produto
    # ========================================================

    for url in todos:

        if _parece_link_de_produto(
            url
        ):

            logger.info(
                "Link de produto encontrado: %s",
                url,
            )

            return url

    # ========================================================
    # PRIORIDADE 3
    # Texto do botão
    # ========================================================

    palavras_produto = (
        "ir para o produto",
        "ver produto",
        "comprar",
        "ver oferta",
        "produto",
    )

    for tag in soup.find_all(
        ["a", "button"]
    ):

        texto = (
            tag.get_text(
                " ",
                strip=True,
            )
            .lower()
        )

        if not texto:
            continue

        if not any(
            palavra in texto
            for palavra in palavras_produto
        ):
            continue

        for atributo in (
            "href",
            "data-href",
            "data-url",
            "data-link",
        ):

            valor = tag.get(
                atributo
            )

            if not valor:
                continue

            url = _normalizar_url(
                base_url,
                valor,
            )

            if not url:
                continue

            if _eh_link_mercadolivre(
                url
            ):

                logger.info(
                    "Link encontrado através do botão '%s': %s",
                    texto,
                    url,
                )

                return url

    return None


# ============================================================
# ABRIR VITRINE
# ============================================================

def _abrir_vitrine(
    link: str,
) -> Tuple[
    requests.Session,
    str,
    str,
]:

    session = _obter_sessao_web()

    logger.info(
        "Abrindo link recebido pelo bot: %s",
        link,
    )

    try:

        response = session.get(
            link,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao abrir o link do Mercado Livre: "
            f"{erro}"
        ) from erro

    logger.info(
        "HTTP ao abrir link: %d",
        response.status_code,
    )

    url_final = (
        response.url
        or link
    )

    logger.info(
        "URL final: %s",
        url_final,
    )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Mercado Livre retornou HTTP "
            f"{response.status_code} ao abrir a vitrine."
        )

    html = (
        response.text
        or ""
    )

    if not html:

        raise MercadoLivreAPIError(
            "A vitrine retornou HTML vazio."
        )

    return (
        session,
        url_final,
        html,
    )


# ============================================================
# ENCONTRAR PRODUTO NA VITRINE
# ============================================================

def encontrar_produto_na_vitrine(
    link: str,
) -> Tuple[
    requests.Session,
    str,
    str,
]:

    (
        session,
        url_vitrine,
        html,
    ) = _abrir_vitrine(
        link
    )

    logger.info(
        "Analisando vitrine: %s",
        url_vitrine,
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # --------------------------------------------------------
    # Procurar link direto do produto
    # --------------------------------------------------------

    url_produto = _encontrar_link_produto(
        soup,
        url_vitrine,
    )

    if not url_produto:

        raise MercadoLivreAPIError(
            "A vitrine foi aberta, mas não foi "
            "encontrado nenhum link para produto."
        )

    logger.info(
        "Produto encontrado na vitrine: %s",
        url_produto,
    )

    return (
        session,
        url_vitrine,
        url_produto,
    )


# ============================================================
# JSON-LD
# ============================================================

def _extrair_json_ld(
    soup: BeautifulSoup,
) -> Dict[str, Any]:

    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:

        texto = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not texto:
            continue

        try:

            dados = json.loads(
                texto
            )

        except Exception:

            continue

        candidatos = []

        if isinstance(
            dados,
            list,
        ):

            candidatos.extend(
                dados
            )

        elif isinstance(
            dados,
            dict,
        ):

            candidatos.append(
                dados
            )

            graph = dados.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):

                candidatos.extend(
                    graph
                )

        for item in candidatos:

            if not isinstance(
                item,
                dict,
            ):
                continue

            tipo = item.get(
                "@type"
            )

            if (
                tipo == "Product"
                or (
                    isinstance(
                        tipo,
                        list,
                    )
                    and "Product" in tipo
                )
            ):

                return item

    return {}


# ============================================================
# EXTRAIR PREÇO
# ============================================================

def _converter_float(
    valor: Any,
) -> Optional[float]:

    if valor is None:
        return None

    try:

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

        texto = (
            texto
            .replace(
                "R$",
                "",
            )
            .strip()
        )

        # 1.234,56
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

        # 1234,56
        elif "," in texto:

            texto = (
                texto
                .replace(
                    ",",
                    ".",
                )
            )

        return float(
            texto
        )

    except (
        ValueError,
        TypeError,
    ):

        return None


# ============================================================
# EXTRAIR DADOS DA PÁGINA DO PRODUTO
# ============================================================

def _extrair_dados_produto(
    session: requests.Session,
    url_produto: str,
) -> Dict[str, Any]:

    logger.info(
        "Abrindo página do produto: %s",
        url_produto,
    )

    try:

        response = session.get(
            url_produto,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao abrir página do produto: "
            f"{erro}"
        ) from erro

    url_final = (
        response.url
        or url_produto
    )

    logger.info(
        "Página do produto HTTP %d | URL final: %s",
        response.status_code,
        url_final,
    )

    # --------------------------------------------------------
    # Não aceitar account-verification
    # --------------------------------------------------------

    if (
        "account-verification"
        in url_final.lower()
    ):

        raise MercadoLivreAPIError(
            "O Mercado Livre redirecionou a página "
            "do produto para account-verification."
        )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Erro ao abrir página do produto: "
            f"HTTP {response.status_code}"
        )

    html = (
        response.text
        or ""
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    json_ld = _extrair_json_ld(
        soup
    )

    # ========================================================
    # ITEM ID
    # ========================================================

    item_id = (
        extrair_item_id_da_string(
            url_final
        )
        or extrair_item_id_da_string(
            html
        )
    )

    # ========================================================
    # TÍTULO
    # ========================================================

    titulo = None

    titulo_elem = soup.find(
        "h1",
        class_="ui-pdp-title",
    )

    if titulo_elem:

        titulo = (
            titulo_elem.get_text(
                " ",
                strip=True,
            )
        )

    if not titulo:

        titulo = json_ld.get(
            "name"
        )

    if not titulo:

        meta = soup.find(
            "meta",
            property="og:title",
        )

        if meta:

            titulo = (
                meta.get(
                    "content"
                )
                or None
            )

    if not titulo:

        titulo = (
            "Produto Mercado Livre"
        )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    meta_image = soup.find(
        "meta",
        property="og:image",
    )

    if meta_image:

        image_url = (
            meta_image.get(
                "content"
            )
            or None
        )

    if not image_url:

        imagem = json_ld.get(
            "image"
        )

        if isinstance(
            imagem,
            list,
        ):

            if imagem:
                image_url = imagem[0]

        elif isinstance(
            imagem,
            str,
        ):

            image_url = imagem

    if not image_url:

        img = soup.find(
            "img",
            class_="ui-pdp-image",
        )

        if img:

            image_url = (
                img.get("src")
                or img.get("data-zoom")
                or img.get("data-src")
            )

    # ========================================================
    # PREÇO
    # ========================================================

    price = None

    meta_price = soup.find(
        "meta",
        itemprop="price",
    )

    if meta_price:

        price = _converter_float(
            meta_price.get(
                "content"
            )
        )

    if price is None:

        offers = json_ld.get(
            "offers"
        )

        if isinstance(
            offers,
            list,
        ):

            offers = (
                offers[0]
                if offers
                else {}
            )

        if isinstance(
            offers,
            dict,
        ):

            price = _converter_float(
                offers.get(
                    "price"
                )
            )

    # ========================================================
    # PREÇO ORIGINAL
    # ========================================================

    original_price = None

    meta_original = soup.find(
        "meta",
        itemprop="price",
    )

    # Alguns anúncios disponibilizam
    # o preço anterior através de classes
    # específicas.

    elementos_preco_original = soup.select(
        ".ui-pdp-price__original-value"
    )

    if elementos_preco_original:

        original_text = (
            elementos_preco_original[0]
            .get_text(
                " ",
                strip=True,
            )
        )

        original_price = _converter_float(
            original_text
        )

    # ========================================================
    # DESCONTO
    # ========================================================

    discount = None

    if (
        price is not None
        and original_price is not None
        and original_price > 0
        and price < original_price
    ):

        discount = round(
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
    # MOEDA
    # ========================================================

    currency_id = (
        json_ld.get(
            "offers",
            {}
        )
        if isinstance(
            json_ld.get(
                "offers"
            ),
            dict,
        )
        else {}
    )

    currency_id = (
        currency_id.get(
            "priceCurrency"
        )
        or "BRL"
    )

    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    if (
        titulo == "Produto Mercado Livre"
        and not image_url
        and price is None
    ):

        raise MercadoLivreAPIError(
            "A página foi aberta, mas não foi possível "
            "extrair dados do produto."
        )

    # ========================================================
    # PRODUTO FINAL
    # ========================================================

    produto = {

        "itemId": item_id,

        "shopId": None,

        "sellerId": None,

        "productName": titulo,

        "title": titulo,

        "price": price,

        "priceMin": price,

        "priceMax": price,

        "originalPrice": original_price,

        "priceDiscountRate": discount,

        "currencyId": currency_id,

        "sales": None,

        "soldQuantity": None,

        "imageUrl": image_url,

        "thumbnail": image_url,

        "productLink": url_final,

        "permalink": url_final,

        "shopName": None,

        "shopType": None,

        "ratingStar": None,

        "categoryId": None,

        "condition": "new",

        "availableQuantity": None,

        "listingTypeId": None,

        "buyingMode": None,

        "siteId": "MLB",

        "mercadolivreData": {
            "jsonLd": json_ld,
            "url": url_final,
        },
    }

    logger.info(
        "Produto extraído com sucesso: %s",
        titulo,
    )

    logger.info(
        "Preço encontrado: %s",
        price,
    )

    logger.info(
        "Imagem encontrada: %s",
        bool(image_url),
    )

    logger.info(
        "Item ID encontrado: %s",
        item_id,
    )

    return produto


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

def buscar_produto_por_link(
    link: str,
) -> Dict[str, Any]:

    if not link:

        raise MercadoLivreAPIError(
            "Link de entrada está vazio."
        )

    link = (
        str(link)
        .strip()
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "Iniciando busca pelo link recebido."
    )

    logger.info(
        "Link original: %s",
        link,
    )

    # ========================================================
    # 1. ABRIR VITRINE
    # ========================================================

    (
        session,
        url_vitrine,
        url_produto,
    ) = encontrar_produto_na_vitrine(
        link
    )

    logger.info(
        "Vitrine encontrada: %s",
        url_vitrine,
    )

    logger.info(
        "Link do produto encontrado: %s",
        url_produto,
    )

    # ========================================================
    # 2. ABRIR PRODUTO
    # ========================================================

    produto = _extrair_dados_produto(
        session,
        url_produto,
    )

    # ========================================================
    # 3. PRESERVAR LINKS
    # ========================================================

    produto.update({

        "manualAffiliateLink": link,

        "affiliateLink": link,

        "originalAffiliateLink": link,

        "resolvedVitrineLink": url_vitrine,

        "resolvedProductLink": url_produto,

        "productLink": (
            produto.get(
                "productLink"
            )
            or url_produto
        ),

        "permalink": (
            produto.get(
                "permalink"
            )
            or url_produto
        ),
    })

    # ========================================================
    # 4. GARANTIR ITEM ID
    # ========================================================

    if not produto.get(
        "itemId"
    ):

        produto["itemId"] = (
            extrair_item_id_da_string(
                url_produto
            )
        )

    logger.info(
        "=================================================="
    )

    logger.info(
        "Produto processado com sucesso: %s",
        produto.get(
            "productName"
        ),
    )

    logger.info(
        "Item ID: %s",
        produto.get(
            "itemId"
        ),
    )

    logger.info(
        "Preço: %s",
        produto.get(
            "price"
        ),
    )

    logger.info(
        "Imagem: %s",
        produto.get(
            "imageUrl"
        ),
    )

    logger.info(
        "Link produto: %s",
        produto.get(
            "productLink"
        ),
    )

    logger.info(
        "=================================================="
    )

    return produto
