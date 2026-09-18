import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

HTTP_TIMEOUT = 30

PLAYWRIGHT_TIMEOUT = HTTP_TIMEOUT * 1000

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
# EXTRAIR LINKS DA VITRINE
#
# Mantida para fallback.
# O fluxo principal agora usa Playwright.
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
        # LINK COM MLB
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
        # TEXTO RELACIONADO A PRODUTO
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

        # ----------------------------------------------------
        # URL RELACIONADA A PRODUTO
        # ----------------------------------------------------

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
    # 2. DATA-HREF / DATA-URL
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
#
# Fallback usando HTML.
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
    # PRIORIDADE: IR PARA O PRODUTO
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

            return (
                href,
                item_id,
            )

    # ========================================================
    # QUALQUER LINK COM MLB
    # ========================================================

    for href, texto in candidatos:

        item_id = (
            extrair_item_id_da_string(
                href
            )
        )

        if item_id:

            return (
                href,
                item_id,
            )

    # ========================================================
    # PRIMEIRO CANDIDATO
    # ========================================================

    href, texto = candidatos[0]

    item_id = (
        extrair_item_id_da_string(
            href
        )
    )

    return (
        href,
        item_id,
    )


# ============================================================
# ENCONTRAR BOTÃO "IR PARA O PRODUTO"
# ============================================================

def _encontrar_botao_ir_para_produto(
    page,
):
    """
    Procura o botão/link "Ir para o produto"
    no DOM renderizado pelo navegador.

    Tenta várias estratégias porque a estrutura
    HTML da vitrine pode variar.
    """

    padrao = re.compile(
        r"ir\s+para\s+o\s+produto",
        re.IGNORECASE,
    )

    candidatos = []

    # ========================================================
    # 1. LINK PELO ROLE
    # ========================================================

    candidatos.append(
        page.get_by_role(
            "link",
            name=padrao,
        )
    )

    # ========================================================
    # 2. BOTÃO PELO ROLE
    # ========================================================

    candidatos.append(
        page.get_by_role(
            "button",
            name=padrao,
        )
    )

    # ========================================================
    # 3. TEXTO
    # ========================================================

    candidatos.append(
        page.get_by_text(
            padrao
        )
    )

    # ========================================================
    # TESTAR CANDIDATOS
    # ========================================================

    for candidato in candidatos:

        try:

            quantidade = (
                candidato.count()
            )

        except Exception:

            continue

        if quantidade <= 0:
            continue

        for indice in range(
            min(quantidade, 10)
        ):

            try:

                elemento = (
                    candidato.nth(indice)
                )

                if elemento.is_visible(
                    timeout=2000
                ):

                    return elemento

            except Exception:

                continue

    return None


# ============================================================
# ABRIR VITRINE E CLICAR NO PRODUTO
# ============================================================

def _abrir_vitrine_e_clicar_produto(
    link: str,
) -> Tuple[str, str, str]:

    logger.info(
        "Abrindo vitrine com Playwright: %s",
        link,
    )

    with sync_playwright() as p:

        browser = None
        context = None

        try:

            # ====================================================
            # INICIAR CHROMIUM
            # ====================================================

            browser = p.chromium.launch(
                headless=True,
            )

            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                locale="pt-BR",
                viewport={
                    "width": 1366,
                    "height": 768,
                },
            )

            page = context.new_page()

            page.set_default_timeout(
                15000
            )

            # ====================================================
            # ABRIR VITRINE
            # ====================================================

            logger.info(
                "Navegando para a vitrine..."
            )

            page.goto(
                link,
                wait_until="domcontentloaded",
                timeout=PLAYWRIGHT_TIMEOUT,
            )

            # ====================================================
            # ESPERAR CARREGAMENTO
            # ====================================================

            try:

                page.wait_for_load_state(
                    "networkidle",
                    timeout=10000,
                )

            except PlaywrightTimeoutError:

                logger.warning(
                    "A vitrine não atingiu "
                    "networkidle. Continuando."
                )

            url_vitrine = page.url

            logger.info(
                "Vitrine carregada: %s",
                url_vitrine,
            )

            # ====================================================
            # ACCOUNT VERIFICATION
            # ====================================================

            if _eh_account_verification(
                url_vitrine
            ):

                raise MercadoLivreAPIError(
                    "O Mercado Livre redirecionou "
                    "a vitrine para account-verification."
                )

            # ====================================================
            # CASO O LINK JÁ SEJA UM PRODUTO
            # ====================================================

            item_id_direto = (
                extrair_item_id_da_string(
                    url_vitrine
                )
            )

            if item_id_direto:

                logger.info(
                    "O link recebido já é "
                    "um produto: %s",
                    item_id_direto,
                )

                try:

                    page.locator(
                        "h1.ui-pdp-title"
                    ).wait_for(
                        state="visible",
                        timeout=15000,
                    )

                except PlaywrightTimeoutError:

                    logger.warning(
                        "Título do produto não apareceu."
                    )

                return (
                    url_vitrine,
                    url_vitrine,
                    page.content(),
                )

            # ====================================================
            # PROCURAR BOTÃO
            # ====================================================

            logger.info(
                "Procurando 'Ir para o produto'..."
            )

            botao = (
                _encontrar_botao_ir_para_produto(
                    page
                )
            )

            # ====================================================
            # FALLBACK:
            # PROCURAR LINKS COM MLB
            # ====================================================

            if botao is None:

                logger.warning(
                    "Botão 'Ir para o produto' "
                    "não encontrado pelo texto."
                )

                links = page.locator(
                    "a[href]"
                )

                quantidade = (
                    links.count()
                )

                for indice in range(
                    min(quantidade, 500)
                ):

                    try:

                        elemento = (
                            links.nth(indice)
                        )

                        href = (
                            elemento.get_attribute(
                                "href"
                            )
                            or ""
                        )

                        href = _normalizar_link(
                            href,
                            page.url,
                        )

                        if (
                            href
                            and _eh_link_mercadolivre(
                                href
                            )
                            and extrair_item_id_da_string(
                                href
                            )
                        ):

                            logger.info(
                                "Link MLB encontrado "
                                "como fallback: %s",
                                href,
                            )

                            # =================================================
                            # CLICAR NO LINK REAL
                            # =================================================

                            botao = elemento

                            break

                    except Exception:

                        continue

            if botao is None:

                # ====================================================
                # ÚLTIMO FALLBACK:
                # ANALISAR HTML RENDERIZADO
                # ====================================================

                html_vitrine = (
                    page.content()
                )

                link_produto, item_id = (
                    _encontrar_produto_na_vitrine(
                        html_vitrine,
                        page.url,
                    )
                )

                if link_produto:

                    logger.warning(
                        "Usando link encontrado "
                        "no HTML renderizado: %s",
                        link_produto,
                    )

                    # Abre o link dentro do mesmo
                    # contexto do navegador.
                    page.goto(
                        link_produto,
                        wait_until="domcontentloaded",
                        timeout=PLAYWRIGHT_TIMEOUT,
                    )

                    try:

                        page.wait_for_load_state(
                            "networkidle",
                            timeout=10000,
                        )

                    except PlaywrightTimeoutError:
                        pass

                    if _eh_account_verification(
                        page.url
                    ):

                        raise MercadoLivreAPIError(
                            "O produto foi redirecionado "
                            "para account-verification."
                        )

                    return (
                        url_vitrine,
                        page.url,
                        page.content(),
                    )

                raise MercadoLivreAPIError(
                    "Não foi encontrado o botão "
                    "'Ir para o produto' "
                    "nem um link de produto."
                )

            # ====================================================
            # GUARDAR URL ANTES DO CLIQUE
            # ====================================================

            url_antes = page.url

            logger.info(
                "Clicando em 'Ir para o produto'..."
            )

            # ====================================================
            # TENTAR DETECTAR NOVA ABA
            # ====================================================

            try:

                with context.expect_page(
                    timeout=3000
                ) as nova_pagina_info:

                    botao.click(
                        timeout=10000
                    )

                nova_pagina = (
                    nova_pagina_info.value
                )

                logger.info(
                    "O produto abriu em uma nova aba."
                )

                try:

                    nova_pagina.wait_for_load_state(
                        "domcontentloaded",
                        timeout=PLAYWRIGHT_TIMEOUT,
                    )

                except PlaywrightTimeoutError:

                    pass

                try:

                    nova_pagina.wait_for_load_state(
                        "networkidle",
                        timeout=10000,
                    )

                except PlaywrightTimeoutError:

                    pass

                page = nova_pagina

            except PlaywrightTimeoutError:

                # ====================================================
                # CLIQUE NA MESMA ABA
                # ====================================================

                logger.info(
                    "Nenhuma nova aba detectada. "
                    "Aguardando navegação na mesma aba."
                )

                try:

                    page.wait_for_url(
                        lambda url: url != url_antes,
                        timeout=15000,
                    )

                except PlaywrightTimeoutError:

                    logger.warning(
                        "A URL não mudou imediatamente "
                        "após o clique."
                    )

                try:

                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=15000,
                    )

                except PlaywrightTimeoutError:

                    pass

                try:

                    page.wait_for_load_state(
                        "networkidle",
                        timeout=10000,
                    )

                except PlaywrightTimeoutError:

                    pass

            except Exception as erro:

                logger.warning(
                    "Erro ao detectar nova aba: %s",
                    erro,
                )

                # Tenta garantir que o clique ocorreu.
                try:

                    botao.click(
                        timeout=10000
                    )

                except Exception:

                    pass

            # ====================================================
            # URL FINAL
            # ====================================================

            url_produto = page.url

            logger.info(
                "URL final após clique: %s",
                url_produto,
            )

            # ====================================================
            # ACCOUNT VERIFICATION
            # ====================================================

            if _eh_account_verification(
                url_produto
            ):

                raise MercadoLivreAPIError(
                    "O clique em 'Ir para o produto' "
                    "levou para account-verification."
                )

            # ====================================================
            # AGUARDAR TÍTULO
            # ====================================================

            try:

                page.locator(
                    "h1.ui-pdp-title"
                ).wait_for(
                    state="visible",
                    timeout=15000,
                )

                logger.info(
                    "Título do produto encontrado."
                )

            except PlaywrightTimeoutError:

                logger.warning(
                    "h1.ui-pdp-title não apareceu."
                )

            # ====================================================
            # MAIS UMA ESPERA PARA CONTEÚDO DINÂMICO
            # ====================================================

            try:

                page.wait_for_timeout(
                    1000
                )

            except Exception:

                pass

            # ====================================================
            # HTML FINAL RENDERIZADO
            # ====================================================

            html_produto = (
                page.content()
            )

            if not html_produto:

                raise MercadoLivreAPIError(
                    "A página do produto "
                    "não retornou HTML."
                )

            return (
                url_vitrine,
                url_produto,
                html_produto,
            )

        except MercadoLivreAPIError:

            raise

        except PlaywrightTimeoutError as erro:

            raise MercadoLivreAPIError(
                "Timeout durante a navegação "
                "com Playwright."
            ) from erro

        except Exception as erro:

            raise MercadoLivreAPIError(
                "Erro durante a navegação "
                f"com Playwright: {erro}"
            ) from erro

        finally:

            if context is not None:

                try:
                    context.close()
                except Exception:
                    pass

            if browser is not None:

                try:
                    browser.close()
                except Exception:
                    pass


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
# CONVERTER PREÇO
# ============================================================

def _converter_preco(
    valor: Any,
) -> Optional[float]:

    if valor is None:
        return None

    if isinstance(
        valor,
        (int, float),
    ):

        return float(valor)

    valor = (
        str(valor)
        .strip()
    )

    if not valor:
        return None

    # Remove símbolos de moeda
    valor = re.sub(
        r"[^\d,.\-]",
        "",
        valor,
    )

    if not valor:
        return None

    # Caso brasileiro:
    # 1.299,90 -> 1299.90
    if (
        "," in valor
        and "." in valor
    ):

        valor = (
            valor
            .replace(".", "")
            .replace(",", ".")
        )

    elif "," in valor:

        valor = valor.replace(
            ",",
            ".",
        )

    try:

        return float(
            valor
        )

    except (
        ValueError,
        TypeError,
    ):

        return None


# ============================================================
# EXTRAIR DADOS DO PRODUTO
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

    # Fallback JSON-LD
    if not titulo:

        titulo = json_ld.get(
            "name"
        )

    # Fallback OG
    if not titulo:

        meta = soup.find(
            "meta",
            property="og:title",
        )

        if meta:

            titulo = meta.get(
                "content"
            )

    # Fallback <title>
    if not titulo:

        elemento_title = soup.find(
            "title"
        )

        if elemento_title:

            titulo = (
                elemento_title.get_text(
                    " ",
                    strip=True,
                )
            )

    # ========================================================
    # IMAGEM
    # ========================================================

    image_url = None

    # OG image
    meta = soup.find(
        "meta",
        property="og:image",
    )

    if meta:

        image_url = meta.get(
            "content"
        )

    # JSON-LD
    if not image_url:

        imagem = json_ld.get(
            "image"
        )

        if isinstance(
            imagem,
            list,
        ):

            if imagem:

                image_url = (
                    imagem[0]
                )

        elif isinstance(
            imagem,
            str,
        ):

            image_url = imagem

    # Fallback img
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

    # --------------------------------------------------------
    # meta itemprop price
    # --------------------------------------------------------

    meta = soup.find(
        "meta",
        itemprop="price",
    )

    if meta:

        price = _converter_preco(
            meta.get("content")
        )

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

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

            price = _converter_preco(
                offers.get("price")
            )

    # --------------------------------------------------------
    # PREÇO VISÍVEL NA PÁGINA
    # --------------------------------------------------------

    if price is None:

        seletores_preco = (
            ".andes-money-amount__fraction",
            ".ui-pdp-price__second-line .andes-money-amount__fraction",
            ".ui-pdp-price__part .andes-money-amount__fraction",
        )

        for seletor in seletores_preco:

            try:

                elementos = soup.select(
                    seletor
                )

            except Exception:

                continue

            if not elementos:
                continue

            partes = []

            for elemento in elementos:

                texto = elemento.get_text(
                    " ",
                    strip=True,
                )

                if texto:
                    partes.append(
                        texto
                    )

            if partes:

                # Normalmente a parte inteira está no primeiro
                # elemento. Se houver separador decimal,
                # tentamos localizar também os centavos.

                inteiro = partes[0]

                centavos = None

                # Procura centavos em elementos próximos.
                pai = elementos[0].parent

                if pai:

                    texto_pai = pai.get_text(
                        " ",
                        strip=True,
                    )

                    match_centavos = re.search(
                        r"[,.](\d{2})(?!\d)",
                        texto_pai,
                    )

                    if match_centavos:

                        centavos = (
                            match_centavos.group(1)
                        )

                if centavos:

                    price = _converter_preco(
                        f"{inteiro},{centavos}"
                    )

                else:

                    price = _converter_preco(
                        inteiro
                    )

                if price is not None:
                    break

    # ========================================================
    # MOEDA
    # ========================================================

    currency = "BRL"

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

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
            or currency
        )

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

    if not currency:

        meta_currency = soup.find(
            "meta",
            itemprop="priceCurrency",
        )

        if meta_currency:

            currency = (
                meta_currency.get(
                    "content"
                )
                or "BRL"
            )

    # ========================================================
    # ITEM ID
    # ========================================================

    item_id_final = (
        item_id
        or extrair_item_id_da_string(
            url_produto
        )
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

    # ========================================================
    # NORMALIZAÇÃO DO TÍTULO
    # ========================================================

    if titulo:

        titulo = re.sub(
            r"\s+",
            " ",
            str(titulo),
        ).strip()

    # ========================================================
    # NORMALIZAÇÃO DA IMAGEM
    # ========================================================

    if image_url:

        image_url = _normalizar_link(
            image_url,
            url_produto,
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    produto = {
        "itemId": item_id_final,

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

        "currencyId": (
            currency
            or "BRL"
        ),

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
            "source": "vitrine_web_playwright",

            "item_id": item_id_final,

            "url": url_produto,

            "title_found": bool(
                titulo
            ),

            "price_found": (
                price is not None
            ),

            "image_found": bool(
                image_url
            ),
        },
    }

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

    # ========================================================
    # 1. ABRIR VITRINE
    #
    # 2. ENCONTRAR "IR PARA O PRODUTO"
    #
    # 3. CLICAR USANDO PLAYWRIGHT
    #
    # 4. AGUARDAR A PÁGINA
    # ========================================================

    (
        url_vitrine,
        link_produto,
        html_produto,
    ) = _abrir_vitrine_e_clicar_produto(
        link
    )

    # ========================================================
    # ITEM ID
    # ========================================================

    item_id = (
        extrair_item_id_da_string(
            link_produto
        )
    )

    logger.info(
        "Vitrine: %s",
        url_vitrine,
    )

    logger.info(
        "Produto: %s",
        link_produto,
    )

    logger.info(
        "Item ID: %s",
        item_id,
    )

    # ========================================================
    # EXTRAIR DADOS
    # ========================================================

    produto = _extrair_dados_produto(
        html=html_produto,
        url_produto=link_produto,
        item_id=item_id,
    )

    # ========================================================
    # PRESERVAR LINKS
    # ========================================================

    produto.update(
        {
            "manualAffiliateLink": link,

            "affiliateLink": link,

            "originalAffiliateLink": link,

            "vitrineLink": url_vitrine,

            "productButtonLink": link_produto,

            "resolvedProductLink": link_produto,
        }
    )

    logger.info(
        "Produto processado com sucesso: %s",
        produto.get(
            "productName"
        ),
    )

    return produto
