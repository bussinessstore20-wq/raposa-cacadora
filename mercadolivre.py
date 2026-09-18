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

    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
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
        }
    )

    return session


# ============================================================
# ITEM ID
# ============================================================

def extrair_item_id_da_string(
    conteudo: str,
) -> Optional[str]:

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

    return None


# ============================================================
# NORMALIZAR LINK
# ============================================================

def _normalizar_link(
    href: str,
    base_url: str,
) -> str:

    href = (
        str(href or "")
        .strip()
    )

    if not href:
        return ""

    if href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        )
    ):
        return ""

    return urljoin(
        base_url,
        href,
    )


# ============================================================
# VERIFICAR SE É MERCADO LIVRE
# ============================================================

def _eh_link_mercadolivre(
    url: str,
) -> bool:

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
        or "meli.la" in host
    )


# ============================================================
# DETECTAR ACCOUNT VERIFICATION
# ============================================================

def _eh_account_verification(
    url: str,
) -> bool:

    url_lower = (
        str(url or "")
        .lower()
    )

    return (
        "/gz/account-verification"
        in url_lower
    )


# ============================================================
# EXTRAIR LINK DO PRODUTO DA VITRINE
# ============================================================

def _extrair_links_de_produto(
    soup: BeautifulSoup,
    url_vitrine: str,
) -> List[Tuple[str, str]]:

    encontrados = []

    vistos = set()

    # ========================================================
    # 1. LINKS <a>
    # ========================================================

    for tag in soup.find_all(
        "a",
        href=True,
    ):

        href_original = str(
            tag.get("href") or ""
        ).strip()

        if not href_original:
            continue

        href = _normalizar_link(
            href_original,
            url_vitrine,
        )

        if not href:
            continue

        if not _eh_link_mercadolivre(
            href
        ):
            continue

        chave = href.split("#")[0]

        if chave in vistos:
            continue

        vistos.add(chave)

        texto = (
            tag.get_text(
                " ",
                strip=True,
            )
            or ""
        )

        # ----------------------------------------------------
        # PRIORIDADE:
        # Links que já possuem MLB
        # ----------------------------------------------------

        item_id = (
            extrair_item_id_da_string(
                href
            )
        )

        if item_id:

            encontrados.append(
                (
                    href,
                    texto,
                )
            )

            continue

        # ----------------------------------------------------
        # PRIORIDADE:
        # href com sinais de produto
        # ----------------------------------------------------

        texto_lower = texto.lower()
        href_lower = href.lower()

        palavras_produto = (
            "ir para o produto",
            "ver produto",
            "comprar",
            "produto",
            "ver oferta",
            "oferta",
        )

        if any(
            palavra in texto_lower
            for palavra in palavras_produto
        ):

            encontrados.append(
                (
                    href,
                    texto,
                )
            )

            continue

        if any(
            palavra in href_lower
            for palavra in (
                "/p/",
                "/produto/",
                "/item/",
                "redirect",
                "product",
            )
        ):

            encontrados.append(
                (
                    href,
                    texto,
                )
            )

    # ========================================================
    # 2. ELEMENTOS COM DATA-HREF
    # ========================================================

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

            href = _normalizar_link(
                str(valor),
                url_vitrine,
            )

            if not href:
                continue

            if not _eh_link_mercadolivre(
                href
            ):
                continue

            chave = href.split("#")[0]

            if chave in vistos:
                continue

            vistos.add(chave)

            encontrados.append(
                (
                    href,
                    tag.get_text(
                        " ",
                        strip=True,
                    )
                    or "",
                )
            )

    return encontrados


# ============================================================
# ENCONTRAR PRODUTO NA VITRINE
# ============================================================

def _encontrar_produto_na_vitrine(
    html: str,
    url_vitrine: str,
) -> Tuple[str, Optional[str]]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidatos = (
        _extrair_links_de_produto(
            soup,
            url_vitrine,
        )
    )

    if not candidatos:

        # ----------------------------------------------------
        # Último recurso:
        # procura MLB no HTML
        # ----------------------------------------------------

        match = re.search(
            r"MLB[-_]?\d{8,10}",
            html,
            re.IGNORECASE,
        )

        if match:

            item_id = (
                extrair_item_id_da_string(
                    match.group(0)
                )
            )

            if item_id:

                logger.warning(
                    "Encontrou MLB no HTML, "
                    "mas não encontrou o href "
                    "do botão do produto: %s",
                    item_id,
                )

                return (
                    "",
                    item_id,
                )

        raise MercadoLivreAPIError(
            "A vitrine foi aberta, mas "
            "nenhum link de produto foi encontrado."
        )

    # ========================================================
    # PRIORIZAR "IR PARA O PRODUTO"
    # ========================================================

    for href, texto in candidatos:

        texto_lower = (
            texto or ""
        ).lower()

        if (
            "ir para o produto"
            in texto_lower
        ):

            item_id = (
                extrair_item_id_da_string(
                    href
                )
            )

            logger.info(
                "Botão 'Ir para o produto' encontrado: %s",
                href,
            )

            if item_id:

                logger.info(
                    "Produto identificado no botão: %s",
                    item_id,
                )

            return (
                href,
                item_id,
            )

    # ========================================================
    # DEPOIS, QUALQUER LINK COM MLB
    # ========================================================

    for href, texto in candidatos:

        item_id = (
            extrair_item_id_da_string(
                href
            )
        )

        if item_id:

            logger.info(
                "Link de produto encontrado na vitrine: %s",
                href,
            )

            return (
                href,
                item_id,
            )

    # ========================================================
    # POR ÚLTIMO, PRIMEIRO CANDIDATO
    # ========================================================

    href, texto = candidatos[0]

    item_id = (
        extrair_item_id_da_string(
            href
        )
    )

    logger.info(
        "Link candidato encontrado na vitrine: %s",
        href,
    )

    return (
        href,
        item_id,
    )


# ============================================================
# RESOLVER LINK DA VITRINE
# ============================================================

def resolver_link_da_vitrine(
    link: str,
) -> Tuple[str, str, Optional[str]]:

    link = (
        str(link or "")
        .strip()
    )

    if not link:

        raise MercadoLivreAPIError(
            "Link de entrada está vazio."
        )

    logger.info(
        "Abrindo link recebido pelo bot: %s",
        link,
    )

    session = _obter_sessao_web()

    try:

        response = session.get(
            link,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao abrir o link recebido: "
            f"{erro}"
        ) from erro

    url_final = (
        response.url
        or link
    )

    logger.info(
        "URL final da vitrine: %s",
        url_final,
    )

    if _eh_account_verification(
        url_final
    ):

        raise MercadoLivreAPIError(
            "O link recebido foi bloqueado "
            "pelo Mercado Livre com "
            "account-verification."
        )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Erro ao abrir a vitrine: "
            f"HTTP {response.status_code}"
        )

    html = (
        response.text
        or ""
    )

    # ========================================================
    # CASO O LINK JÁ SEJA UM PRODUTO
    # ========================================================

    item_id = (
        extrair_item_id_da_string(
            url_final
        )
    )

    if item_id:

        logger.info(
            "Link recebido já aponta "
            "para produto: %s",
            item_id,
        )

        return (
            url_final,
            url_final,
            item_id,
        )

    # ========================================================
    # VITRINE
    # ========================================================

    logger.info(
        "Procurando botão/link "
        "do produto dentro da vitrine..."
    )

    link_produto, item_id = (
        _encontrar_produto_na_vitrine(
            html,
            url_final,
        )
    )

    if not link_produto:

        raise MercadoLivreAPIError(
            "O produto foi encontrado no HTML, "
            "mas o link para abrir o produto "
            "não foi encontrado."
        )

    logger.info(
        "Link real do produto encontrado: %s",
        link_produto,
    )

    return (
        url_final,
        link_produto,
        item_id,
    )


# ============================================================
# ABRIR PÁGINA REAL DO PRODUTO
# ============================================================

def _abrir_pagina_produto(
    session: requests.Session,
    link_produto: str,
) -> Tuple[str, str]:

    logger.info(
        "Abrindo o link real do produto: %s",
        link_produto,
    )

    try:

        response = session.get(
            link_produto,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        raise MercadoLivreAPIError(
            "Erro ao abrir o link do produto: "
            f"{erro}"
        ) from erro

    url_final = (
        response.url
        or link_produto
    )

    logger.info(
        "Página do produto retornou HTTP %d | URL final: %s",
        response.status_code,
        url_final,
    )

    if _eh_account_verification(
        url_final
    ):

        raise MercadoLivreAPIError(
            "O Mercado Livre redirecionou "
            "a página do produto para "
            "account-verification."
        )

    if response.status_code >= 400:

        raise MercadoLivreAPIError(
            "Erro ao abrir página do produto: "
            f"HTTP {response.status_code}"
        )

    return (
        url_final,
        response.text or "",
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
# EXTRAIR DADOS DA PÁGINA
# ============================================================

def _extrair_dados_produto(
    html: str,
    url_produto: str,
    item_id: Optional[str],
) -> Dict[str, Any]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    json_ld = _extrair_json_ld(
        soup
    )

    # ========================================================
    # TÍTULO
    # ========================================================

    titulo = None

    elemento = soup.find(
        "h1",
        class_="ui-pdp-title",
    )

    if elemento:

        titulo = elemento.get_text(
            " ",
            strip=True,
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

            titulo = meta.get(
                "content"
            )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    meta = soup.find(
        "meta",
        property="og:image",
    )

    if meta:

        image_url = meta.get(
            "content"
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

    meta = soup.find(
        "meta",
        itemprop="price",
    )

    if meta:

        valor = meta.get(
            "content"
        )

        try:

            if valor:
                price = float(
                    valor
                )

        except (
            ValueError,
            TypeError,
        ):
            pass

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

            valor = offers.get(
                "price"
            )

            try:

                if valor is not None:

                    price = float(
                        valor
                    )

            except (
                ValueError,
                TypeError,
            ):
                pass

    # ========================================================
    # MOEDA
    # ========================================================

    currency = "BRL"

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

        currency = (
            offers.get(
                "priceCurrency"
            )
            or "BRL"
        )

    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    if not any(
        (
            titulo,
            image_url,
            price is not None,
        )
    ):

        raise MercadoLivreAPIError(
            "A página aberta não contém "
            "dados reconhecíveis do produto."
        )

    return {
        "itemId": (
            item_id
            or extrair_item_id_da_string(
                url_produto
            )
        ),

        "productName": (
            titulo
            or "Produto Mercado Livre"
        ),

        "title": (
            titulo
            or "Produto Mercado Livre"
        ),

        "price": price,

        "priceMin": price,

        "priceMax": price,

        "originalPrice": None,

        "priceDiscountRate": None,

        "currencyId": currency,

        "sales": None,

        "soldQuantity": None,

        "imageUrl": image_url,

        "thumbnail": image_url,

        "productLink": url_produto,

        "permalink": url_produto,

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
            "source": "vitrine_web",
            "item_id": item_id,
            "url": url_produto,
        },
    }


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

    session = _obter_sessao_web()

    # ========================================================
    # 1. ABRE O LINK RECEBIDO PELO BOT
    # ========================================================

    (
        url_vitrine,
        link_produto,
        item_id,
    ) = resolver_link_da_vitrine(
        link
    )

    logger.info(
        "Vitrine encontrada: %s",
        url_vitrine,
    )

    logger.info(
        "Link real do produto: %s",
        link_produto,
    )

    logger.info(
        "Item ID identificado: %s",
        item_id,
    )

    # ========================================================
    # 2. ABRE O LINK DO BOTÃO
    # ========================================================

    url_produto_final, html_produto = (
        _abrir_pagina_produto(
            session,
            link_produto,
        )
    )

    # ========================================================
    # 3. EXTRAI OS DADOS
    # ========================================================

    produto = _extrair_dados_produto(
        html=html_produto,
        url_produto=url_produto_final,
        item_id=item_id,
    )

    # ========================================================
    # 4. PRESERVA LINKS
    # ========================================================

    produto.update(
        {
            "manualAffiliateLink": link,

            "affiliateLink": link,

            "originalAffiliateLink": link,

            "vitrineLink": url_vitrine,

            "productButtonLink": link_produto,

            "resolvedProductLink": (
                url_produto_final
            ),
        }
    )

    logger.info(
        "Produto processado com sucesso: %s",
        produto.get(
            "productName"
        ),
    )

    return produto
