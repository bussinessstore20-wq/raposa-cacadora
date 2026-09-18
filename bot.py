import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from supabase import create_client, Client

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from mercadolivre import (
    buscar_produto_por_link,
    MercadoLivreAPIError,
)

# ============================================================
# OAUTH MERCADO LIVRE
# ============================================================

import mercadolivre_oauth

from mercadolivre_oauth import (
    oauth_login_response,
    oauth_callback_response,
    oauth_status_response,
)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

TELEGRAM_ADMIN_ID = os.getenv(
    "TELEGRAM_ADMIN_ID",
    "",
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "20",
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

MAX_LINKS_POR_ENVIO = 20


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "raposa-cacadora | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client | None = None


def iniciar_supabase():

    global supabase

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY não configurada."
        )

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    mercadolivre_oauth.configurar_supabase(
        supabase
    )

    logger.info(
        "Supabase conectado."
    )


# ============================================================
# CONTROLE DO BOT
# ============================================================

bot_ativo = True

worker_task: asyncio.Task | None = None


# ============================================================
# SERVIDOR HTTP PARA O RENDER + OAUTH
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        try:

            caminho = self.path

            # ------------------------------------------------
            # Separar caminho da query string
            # ------------------------------------------------

            if "?" in caminho:

                rota, query_string = caminho.split(
                    "?",
                    1,
                )

            else:

                rota = caminho
                query_string = ""

            # ------------------------------------------------
            # HEALTH CHECK
            # ------------------------------------------------

            if rota == "/":

                self._responder(
                    status=200,
                    content_type="text/plain; charset=utf-8",
                    body="Raposa Cacadora OK",
                )

                return

            # ------------------------------------------------
            # LOGIN MERCADO LIVRE
            # ------------------------------------------------

            if rota == "/mercadolivre/login":

                status, headers, body = (
                    mercadolivre_oauth.oauth_login_response()
                )

                self._responder(
                    status=status,
                    content_type=headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body=body,
                    extra_headers=headers,
                )

                return

            # ------------------------------------------------
            # CALLBACK MERCADO LIVRE
            # ------------------------------------------------

            if rota == "/mercadolivre/callback":

                status, headers, body = (
                    mercadolivre_oauth.oauth_callback_response(
                     query_string
                    )
                )

                self._responder(
                    status=status,
                    content_type=headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body=body,
                    extra_headers=headers,
                )

                return

            # ------------------------------------------------
            # STATUS OAUTH
            # ------------------------------------------------

            if rota == "/mercadolivre/status":

                status, headers, body = (
                    mercadolivre_oauth.oauth_status_response()
                )

                self._responder(
                    status=status,
                    content_type=headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body=body,
                    extra_headers=headers,
                )

                return

            # ------------------------------------------------
            # ROTA NÃO ENCONTRADA
            # ------------------------------------------------

            self._responder(
                status=404,
                content_type="text/plain; charset=utf-8",
                body="Not Found",
            )

        except Exception as erro:

            logger.exception(
                "Erro no servidor HTTP: %s",
                erro,
            )

            try:

                self._responder(
                    status=500,
                    content_type="text/plain; charset=utf-8",
                    body="Internal Server Error",
                )

            except Exception:
                pass

    def _responder(
        self,
        status: int,
        content_type: str,
        body: str,
        extra_headers: dict | None = None,
    ):

        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        if extra_headers:

            for nome, valor in extra_headers.items():

                if nome.lower() in (
                    "content-type",
                    "content-length",
                ):
                    continue

                self.send_header(
                    nome,
                    valor,
                )

        corpo = body.encode(
            "utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(corpo)),
        )

        self.end_headers()

        self.wfile.write(
            corpo
        )

    def log_message(
        self,
        format,
        *args,
    ):
        return


def iniciar_servidor_http():

    try:

        servidor = ThreadingHTTPServer(
            (
                "0.0.0.0",
                PORT,
            ),
            HealthHandler,
        )

        logger.info(
            "Servidor HTTP iniciado em 0.0.0.0:%d",
            PORT,
        )

        logger.info(
            "Health check: /"
        )

        logger.info(
            "Mercado Livre OAuth: /mercadolivre/login"
        )

        servidor.serve_forever()

    except Exception as erro:

        logger.exception(
            "Erro no servidor HTTP: %s",
            erro,
        )


# ============================================================
# VALIDAÇÃO DA CONFIGURAÇÃO
# ============================================================

def validar_configuracao():

    erros = []

    if not TELEGRAM_TOKEN:
        erros.append(
            "TELEGRAM_TOKEN não configurado."
        )

    if not TELEGRAM_CHAT_ID:
        erros.append(
            "TELEGRAM_CHAT_ID não configurado."
        )

    if not TELEGRAM_ADMIN_ID:
        erros.append(
            "TELEGRAM_ADMIN_ID não configurado."
        )

    if not SUPABASE_URL:
        erros.append(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        erros.append(
            "SUPABASE_KEY não configurada."
        )

    # --------------------------------------------------------
    # OAUTH MERCADO LIVRE
    # --------------------------------------------------------

    oauth_client_id = os.getenv(
        "MERCADOLIVRE_CLIENT_ID",
        "",
    ).strip()

    oauth_client_secret = os.getenv(
        "MERCADOLIVRE_CLIENT_SECRET",
        "",
    ).strip()

    oauth_redirect_uri = os.getenv(
        "MERCADOLIVRE_REDIRECT_URI",
        "",
    ).strip()

    if not oauth_client_id:

        erros.append(
            "MERCADOLIVRE_CLIENT_ID não configurado."
        )

    if not oauth_client_secret:

        erros.append(
            "MERCADOLIVRE_CLIENT_SECRET não configurado."
        )

    if not oauth_redirect_uri:

        erros.append(
            "MERCADOLIVRE_REDIRECT_URI não configurado."
        )

    elif oauth_redirect_uri != (
        "https://raposa-cacadora.onrender.com/"
        "mercadolivre/callback"
    ):

        erros.append(
            "MERCADOLIVRE_REDIRECT_URI inválido. "
            "Use exatamente "
            "https://raposa-cacadora.onrender.com/"
            "mercadolivre/callback"
        )

    if INTERVALO_MINUTOS < 1:

        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0."
        )

    if erros:

        for erro in erros:

            logger.error(
                erro
            )

        raise RuntimeError(
            "Configuração inválida."
        )

    try:

        int(
            TELEGRAM_ADMIN_ID
        )

    except ValueError:

        raise RuntimeError(
            "TELEGRAM_ADMIN_ID deve ser numérico."
        )

    logger.info(
        "Configuração validada."
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS,
    )

    logger.info(
        "Porta HTTP: %d",
        PORT,
    )

    logger.info(
        "OAuth Mercado Livre configurado."
    )


# ============================================================
# AUTORIZAÇÃO
# ============================================================

def usuario_autorizado(
    update: Update,
) -> bool:

    if not update.effective_user:
        return False

    try:

        admin_id = int(
            TELEGRAM_ADMIN_ID
        )

    except ValueError:

        return False

    return (
        update.effective_user.id
        == admin_id
    )


# ============================================================
# EXTRAIR LINKS DO MERCADO LIVRE
# ============================================================

def extrair_links(
    texto: str,
) -> list[str]:

    if not texto:
        return []

    links = []

    for parte in texto.split():

        link = (
            parte
            .strip()
            .strip("<>")
            .strip()
        )

        while link and link[-1] in (
            ",",
            ".",
            ";",
            ")",
            "]",
            "}",
        ):

            link = link[:-1]

        if not (
            link.startswith("http://")
            or link.startswith("https://")
        ):

            continue

        link_lower = link.lower()

        if "meli.la/" in link_lower:

            links.append(link)

            continue

        if (
            "mercadolivre.com.br" in link_lower
            or "mercadolibre.com" in link_lower
        ):

            links.append(link)

    resultado = []
    vistos = set()

    for link in links:

        chave = link.strip()

        if chave not in vistos:

            vistos.add(chave)
            resultado.append(chave)

    return resultado


# ============================================================
# SUPABASE - INSERIR LINKS
# ============================================================

def inserir_links(
    links: list[str],
) -> tuple[int, int, list[str]]:

    if supabase is None:

        raise RuntimeError(
            "Supabase não inicializado."
        )

    adicionados = 0
    duplicados = 0
    erros = []

    for link in links:

        try:

            resposta = (
                supabase
                .table("produtos_fila")
                .insert(
                    {
                        "link": link,
                        "status": "pending",
                    }
                )
                .execute()
            )

            if resposta.data:

                adicionados += 1

        except Exception as erro:

            mensagem_erro = str(
                erro
            )

            texto = mensagem_erro.lower()

            if (
                "duplicate" in texto
                or "unique" in texto
                or "23505" in mensagem_erro
            ):

                duplicados += 1

                logger.info(
                    "Link já existente."
                )

            else:

                logger.exception(
                    "Erro ao inserir link."
                )

                erros.append(link)

    return (
        adicionados,
        duplicados,
        erros,
    )


# ============================================================
# SUPABASE - BUSCAR PRÓXIMO PRODUTO
# ============================================================

def buscar_proximo_produto():

    if supabase is None:

        raise RuntimeError(
            "Supabase não inicializado."
        )

    resposta = (
        supabase
        .table("produtos_fila")
        .select("*")
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if not resposta.data:

        return None

    return resposta.data[0]


# ============================================================
# SUPABASE - MARCAR PROCESSANDO
# ============================================================

def marcar_processando(
    produto_id: int,
) -> bool:

    if supabase is None:

        return False

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "processing",
                "processing_at": agora,
            }
        )
        .eq("id", produto_id)
        .eq("status", "pending")
        .execute()
    )

    return bool(
        resposta.data
    )


# ============================================================
# SUPABASE - MARCAR PUBLICADO
# ============================================================

def marcar_publicado(
    produto_id: int,
    produto: dict[str, Any],
    telegram_message_id: int | None,
):

    if supabase is None:
        return

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    dados = {

        "status": "published",

        "product_name": (
            produto.get(
                "productName"
            )
            or "Produto"
        ),

        "shop_id": produto.get(
            "shopId"
        ),

        "item_id": produto.get(
            "itemId"
        ),

        "image_url": produto.get(
            "imageUrl"
        ),

        "telegram_message_id": (
            telegram_message_id
        ),

        "telegram_chat_id": (
            TELEGRAM_CHAT_ID
        ),

        "published_at": agora,

        "processing_at": None,

        "erro": None,
    }

    (
        supabase
        .table("produtos_fila")
        .update(dados)
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - MARCAR ERRO
# ============================================================

def marcar_erro(
    produto_id: int,
    erro: str,
):

    if supabase is None:
        return

    resposta = (
        supabase
        .table("produtos_fila")
        .select("tentativas")
        .eq("id", produto_id)
        .limit(1)
        .execute()
    )

    tentativas = 0

    if resposta.data:

        tentativas = int(
            resposta.data[0].get(
                "tentativas",
                0,
            )
            or 0
        )

    tentativas += 1

    (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "error",
                "tentativas": tentativas,
                "erro": erro[:2000],
                "processing_at": None,
            }
        )
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - RECUPERAR PROCESSAMENTOS PRESOS
# ============================================================

def recuperar_processamentos_presos():

    if supabase is None:
        return

    limite = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            minutes=max(
                INTERVALO_MINUTOS * 2,
                20,
            )
        )
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .select("id")
        .eq("status", "processing")
        .lt("processing_at", limite)
        .execute()
    )

    if not resposta.data:
        return

    for produto in resposta.data:

        produto_id = produto["id"]

        (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "processing_at": None,
                }
            )
            .eq("id", produto_id)
            .eq("status", "processing")
            .execute()
        )

        logger.warning(
            "Produto %s recuperado de processing.",
            produto_id,
        )


# ============================================================
# NÚMEROS
# ============================================================

def numero(
    valor,
    padrao=0.0,
):

    try:

        if valor is None:
            return padrao

        texto = str(
            valor
        ).strip()

        if not texto:
            return padrao

        return float(
            texto.replace(
                ",",
                ".",
            )
        )

    except Exception:

        return padrao


def inteiro(
    valor,
    padrao=0,
):

    try:

        if valor is None:
            return padrao

        return int(
            float(valor)
        )

    except Exception:

        return padrao


# ============================================================
# MOEDA
# ============================================================

def moeda(
    valor,
):

    valor = numero(
        valor
    )

    texto = (
        f"{valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {texto}"


# ============================================================
# VENDAS
# ============================================================

def formatar_vendas(
    vendas,
):

    vendas = inteiro(
        vendas
    )

    return (
        f"{vendas:,}"
        .replace(",", ".")
    )


# ============================================================
# MENSAGEM DO PRODUTO
# ============================================================

def montar_mensagem(
    produto,
):

    nome = (
        produto.get(
            "productName"
        )
        or "Produto"
    )

    preco = numero(
        produto.get(
            "price"
        )
    )

    preco_min = numero(
        produto.get(
            "priceMin"
        )
    )

    desconto = numero(
        produto.get(
            "priceDiscountRate"
        )
    )

    avaliacao = numero(
        produto.get(
            "ratingStar"
        )
    )

    vendas = inteiro(
        produto.get(
            "sales"
        )
    )

    loja = (
        produto.get(
            "shopName"
        )
        or "Mercado Livre"
    )

    preco_atual = preco

    if preco_min > 0:
        preco_atual = preco_min

    if (
        desconto > 0
        and desconto < 100
        and preco_atual > 0
    ):

        preco_anterior = (
            preco_atual
            / (
                1
                - desconto / 100
            )
        )

    else:

        preco_anterior = preco_atual

    if avaliacao > 0:

        avaliacao_texto = (
            f"{avaliacao:.1f}"
        )

    else:

        avaliacao_texto = "N/D"

    mensagem = (
        "🔥 <b>OFERTA EM DESTAQUE</b>\n"
        "\n"
        f"✨ <b>{nome}</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"❌ De: <s>{moeda(preco_anterior)}</s>\n"
        f"💰 <b>Por apenas: {moeda(preco_atual)}</b>\n"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"⭐ <b>{avaliacao_texto}</b>/5 de avaliação\n"
        f"📦 <b>{formatar_vendas(vendas)}</b> vendas\n"
        f"🏪 <b>{loja}</b>\n"
        "\n"
        "🚨 <b>Preço sujeito a alteração.</b>\n"
        "⚡ Aproveite enquanto estiver disponível!"
    )

    return mensagem


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str,
):

    mensagem = montar_mensagem(
        produto
    )

    image_url = (
        produto.get(
            "imageUrl"
        )
        or ""
    )

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 COMPRAR AGORA",
                    url=link_afiliado,
                )
            ],
        ]
    )

    if image_url:

        try:

            mensagem_enviada = (
                await bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=image_url,
                    caption=mensagem,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado,
                )
            )

            logger.info(
                "Produto publicado com imagem e botão."
            )

            return (
                True,
                mensagem_enviada.message_id,
            )

        except Exception as erro:

            logger.warning(
                "Falha ao enviar imagem: %s",
                erro,
            )

    try:

        mensagem_enviada = (
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
                disable_web_page_preview=True,
            )
        )

        logger.info(
            "Produto publicado somente como texto."
        )

        return (
            True,
            mensagem_enviada.message_id,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao publicar produto: %s",
            erro,
        )

        return (
            False,
            None,
        )


# ============================================================
# NOTIFICAÇÃO PARA ADMIN
# ============================================================

async def enviar_notificacao_admin(
    bot: Bot,
    texto: str,
):
    try:
        await bot.send_message(
            chat_id=int(TELEGRAM_ADMIN_ID),
            text=texto,
            disable_web_page_preview=True,
        )

    except Exception as erro:
        logger.warning(
            "Não foi possível notificar admin: %s",
            erro,
        )


# ============================================================
# PROCESSAR PRODUTO
# ============================================================

async def processar_produto(
    bot: Bot,
    produto_fila: dict[str, Any],
):

    produto_id = produto_fila["id"]
    link = produto_fila["link"]

    logger.info(
        "=========================================="
    )

    logger.info(
        "Processando produto ID %s",
        produto_id,
    )

    # Não registrar o link completo em logs públicos.
    logger.info(
        "Link recebido para processamento."
    )

    # ========================================================
    # RESERVAR PRODUTO
    # ========================================================

    reservado = await asyncio.to_thread(
        marcar_processando,
        produto_id,
    )

    if not reservado:

        logger.info(
            "Produto %s não pôde ser reservado.",
            produto_id,
        )

        return False

    try:

        # ====================================================
        # BUSCAR PRODUTO
        # ====================================================

        produto = await asyncio.to_thread(
            buscar_produto_por_link,
            link,
        )

        if not produto:

            raise MercadoLivreAPIError(
                "Produto não encontrado."
            )

        logger.info(
            "Produto encontrado: %s",
            produto.get(
                "productName",
                "Produto",
            ),
        )

        # ====================================================
        # PUBLICAR
        # ====================================================

        sucesso, message_id = (
            await publicar_produto(
                bot=bot,
                produto=produto,
                link_afiliado=link,
            )
        )

        if not sucesso:

            raise RuntimeError(
                "Falha ao publicar no Telegram."
            )

        # ====================================================
        # MARCAR PUBLICADO
        # ====================================================

        await asyncio.to_thread(
            marcar_publicado,
            produto_id,
            produto,
            message_id,
        )

        logger.info(
            "Produto %s marcado como publicado.",
            produto_id,
        )

        # ====================================================
        # ADMIN
        # ====================================================

        await enviar_notificacao_admin(
            bot,
            (
                "✅ <b>PRODUTO PUBLICADO</b>\n"
                "\n"
                f"📦 <b>{produto.get('productName', 'Produto')}</b>\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"🏷️ Item: <b>{produto.get('itemId', 'N/D')}</b>\n"
                f"📨 Mensagem: <b>#{message_id}</b>"
            ),
        )

        return True

    except MercadoLivreAPIError as erro:

        logger.error(
            "Erro do Mercado Livre: %s",
            erro,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO NO MERCADO LIVRE</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False

    except Exception as erro:

        logger.exception(
            "Erro ao processar produto %s",
            produto_id,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False


# ============================================================
# TECLADO PRINCIPAL
# ============================================================

def teclado_controle():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ INICIAR",
                    callback_data="bot_iniciar",
                ),
                InlineKeyboardButton(
                    "⏹️ STOP",
                    callback_data="bot_stop",
                ),
            ],
        ]
    )


# ============================================================
# /START
# ============================================================

async def comando_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        "Envie um ou vários links do "
        "<b>Mercado Livre</b> para colocar na fila.\n"
        "\n"
        f"📦 Máximo por envio: <b>{MAX_LINKS_POR_ENVIO}</b>\n"
        f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n"
        "\n"
        "O bot salva tudo no Supabase, então "
        "a fila continua mesmo se o Render reiniciar.\n"
        "\n"
        "Use os botões abaixo para controlar a publicação."
    )

    await update.message.reply_text(
        mensagem,
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /STATUS
# ============================================================

async def comando_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    if update.message is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select("status")
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        total = len(
            registros
        )

        publicados = sum(
            1
            for item in registros
            if item.get("status")
            == "published"
        )

        pendentes = sum(
            1
            for item in registros
            if item.get("status")
            == "pending"
        )

        processando = sum(
            1
            for item in registros
            if item.get("status")
            == "processing"
        )

        erros = sum(
            1
            for item in registros
            if item.get("status")
            == "error"
        )

        estado = (
            "🟢 ATIVO"
            if bot_ativo
            else "🔴 PARADO"
        )

        mensagem = (
            "🦊 <b>RAPOSA CAÇADORA</b>\n"
            "\n"
            "📊 <b>STATUS DA FILA</b>\n"
            "\n"
            f"🤖 Bot: <b>{estado}</b>\n"
            f"📦 Total: <b>{total}</b>\n"
            f"✅ Publicados: <b>{publicados}</b>\n"
            f"⏳ Aguardando: <b>{pendentes}</b>\n"
            f"🔄 Processando: <b>{processando}</b>\n"
            f"❌ Erros: <b>{erros}</b>\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /status"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar status:\n{erro}"
        )


# ============================================================
# /FILA
# ============================================================

async def comando_fila(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    if update.message is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,product_name,status,created_at"
            )
            .order("id", desc=False)
            .limit(100)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "🦊 A fila está vazia."
            )

            return

        linhas = [
            "🦊 <b>FILA DE PRODUTOS</b>",
            "",
        ]

        simbolos = {
            "pending": "⏳",
            "processing": "🔄",
            "published": "✅",
            "error": "❌",
        }

        for item in registros:

            simbolo = simbolos.get(
                item.get("status"),
                "❓",
            )

            nome = (
                item.get(
                    "product_name"
                )
                or "Aguardando processamento"
            )

            if len(nome) > 45:

                nome = (
                    nome[:42]
                    + "..."
                )

            linhas.append(
                f"{simbolo} #{item['id']} — {nome}"
            )

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /fila"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar fila:\n{erro}"
        )


# ============================================================
# /ERROS
# ============================================================

async def comando_erros(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    if update.message is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,erro,tentativas"
            )
            .eq("status", "error")
            .order("id", desc=False)
            .limit(20)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "✅ Não existem produtos com erro."
            )

            return

        linhas = [
            "⚠️ <b>PRODUTOS COM ERRO</b>",
            "",
        ]

        for item in registros:

            erro = (
                item.get("erro")
                or "Erro desconhecido"
            )

            if len(erro) > 300:

                erro = (
                    erro[:297]
                    + "..."
                )

            linhas.append(
                f"❌ <b>#{item['id']}</b>"
            )

            linhas.append(
                f"Tentativas: {item.get('tentativas', 0)}"
            )

            linhas.append(
                f"Erro: {erro}"
            )

            linhas.append("")

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /erros"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar erros:\n{erro}"
        )


# ============================================================
# /RETRY
# ============================================================

async def comando_retry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    if update.message is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "erro": None,
                    "processing_at": None,
                }
            )
            .eq("status", "error")
            .execute()
        )

        quantidade = len(
            resposta.data
            or []
        )

        await update.message.reply_text(
            (
                "🔄 <b>REPROCESSAMENTO</b>\n"
                "\n"
                f"✅ {quantidade} produto(s) "
                "voltaram para a fila.\n"
                "\n"
                "Use ▶️ INICIAR para liberar a publicação."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /retry"
        )

        await update.message.reply_text(
            f"❌ Erro ao reprocessar:\n{erro}"
        )


# ============================================================
# /STOP
# ============================================================

async def comando_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    bot_ativo = False

    logger.warning(
        "Publicação pausada pelo administrador."
    )

    await update.message.reply_text(
        (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "Nenhum novo produto será publicado.\n"
            "Os produtos que já estão no Supabase "
            "continuam na fila.\n"
            "\n"
            "▶️ Use /iniciar ou o botão INICIAR "
            "para continuar."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /INICIAR
# ============================================================

async def comando_iniciar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    bot_ativo = True
    worker_task = None
    
    logger.info(
        "Publicação iniciada pelo administrador."
    )

    await update.message.reply_text(
        (
            "▶️ <b>PUBLICAÇÃO INICIADA</b>\n"
            "\n"
            "A Raposa Caçadora está ativa.\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n"
            "\n"
            "Os produtos pendentes serão processados "
            "automaticamente."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# CALLBACK DOS BOTÕES
# ============================================================

async def callback_controle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    query = update.callback_query

    if query is None:
        return

    if not update.effective_user:
        return

    try:

        await query.answer()

    except Exception:

        pass

    if not usuario_autorizado(update):

        try:

            await query.answer(
                "Você não tem autorização.",
                show_alert=True,
            )

        except Exception:

            pass

        return

    if query.data == "bot_iniciar":

        bot_ativo = True

        logger.info(
            "Publicação iniciada pelo botão."
        )

        texto = (
            "▶️ <b>BOT ATIVADO</b>\n"
            "\n"
            "A publicação automática foi ativada.\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

        try:

            await query.edit_message_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        except Exception as erro:

            logger.warning(
                "Não foi possível editar mensagem: %s",
                erro,
            )

        return

    if query.data == "bot_stop":

        bot_ativo = False

        logger.warning(
            "Publicação parada pelo botão."
        )

        texto = (
            "⏹️ <b>BOT PARADO</b>\n"
            "\n"
            "A publicação automática foi pausada.\n"
            "\n"
            "Os produtos permanecem no Supabase."
        )

        try:

            await query.edit_message_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        except Exception as erro:

            logger.warning(
                "Não foi possível editar mensagem: %s",
                erro,
            )

        return


# ============================================================
# RECEBER LINKS
# ============================================================

async def receber_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    texto = (
        update.message.text
        or ""
    ).strip()

    if not texto:
        return

    links = extrair_links(
        texto
    )

    if not links:

        await update.message.reply_text(
            (
                "⚠️ <b>NENHUM LINK VÁLIDO</b>\n"
                "\n"
                "Envie um link do Mercado Livre."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    if len(links) > MAX_LINKS_POR_ENVIO:

        links = links[
            :MAX_LINKS_POR_ENVIO
        ]

        aviso_limite = (
            f"\n\n⚠️ Apenas os primeiros "
            f"<b>{MAX_LINKS_POR_ENVIO}</b> links "
            "foram adicionados."
        )

    else:

        aviso_limite = ""

    try:

        adicionados, duplicados, erros = (
            await asyncio.to_thread(
                inserir_links,
                links,
            )
        )

    except Exception as erro:

        logger.exception(
            "Erro ao inserir links."
        )

        await update.message.reply_text(
            (
                "❌ <b>ERRO AO SALVAR LINKS</b>\n"
                "\n"
                f"{str(erro)[:1500]}"
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    linhas = [
        "🦊 <b>LINKS RECEBIDOS</b>",
        "",
        f"✅ Adicionados: <b>{adicionados}</b>",
        f"♻️ Duplicados: <b>{duplicados}</b>",
        f"❌ Erros: <b>{len(erros)}</b>",
        "",
        "Os links foram colocados na fila.",
        aviso_limite,
    ]

    await update.message.reply_text(
        "\n".join(linhas),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# WORKER AUTOMÁTICO
# ============================================================

async def worker_publicacao(
    application: Application,
):

    logger.info(
        "🚀🚀🚀 WORKER DE PUBLICAÇÃO ENTROU NA FUNÇÃO."
    )

    while True:

        try:

            logger.info(
                "🔎 Worker executando. bot_ativo=%s",
                bot_ativo,
            )

            if not bot_ativo:

                logger.info(
                    "⏸️ Worker pausado porque bot_ativo=False."
                )

                await asyncio.sleep(5)

                continue

            produto_fila = await asyncio.to_thread(
                buscar_proximo_produto
            )

            logger.info(
                "🔎 Resultado da busca: %s",
                (
                    produto_fila.get("id")
                    if produto_fila
                    else "NENHUM"
                ),
            )

            if not produto_fila:

                await asyncio.sleep(30)

                continue

            logger.info(
                "📦 ENCONTROU PRODUTO ID=%s",
                produto_fila["id"],
            )

            await processar_produto(
                bot=application.bot,
                produto_fila=produto_fila,
            )

            await asyncio.sleep(
                INTERVALO_MINUTOS * 60
            )

        except asyncio.CancelledError:

            logger.info(
                "🛑 Worker cancelado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "❌ Erro no worker: %s",
                erro,
            )

            await asyncio.sleep(30)

# ============================================================
# INICIAR WORKER
# ============================================================

async def iniciar_worker(
    application: Application,
):
    """
    Inicia o worker automático.
    """

    global worker_task

    if worker_task is not None and not worker_task.done():

        logger.warning(
            "Worker já está em execução."
        )

        return

    worker_task = asyncio.create_task(
        worker_publicacao(application)
    )

    logger.info(
        "Task do worker criada."
    )


# ============================================================
# PARAR WORKER
# ============================================================

async def parar_worker():

    global worker_task

    if worker_task is None:
        return

    if worker_task.done():

        worker_task = None

        return

    logger.info(
        "Cancelando worker..."
    )

    worker_task.cancel()

    try:

        await worker_task

    except asyncio.CancelledError:

        pass

    except Exception as erro:

        logger.exception(
            "Erro ao encerrar worker: %s",
            erro,
        )

    worker_task = None

    logger.info(
        "Worker encerrado."
    )

# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):
    """
    Executado depois que o Application do Telegram
    é inicializado.
    """

    logger.info(
        "Inicializando serviços do bot..."
    )

    await iniciar_worker(
        application
    )

    logger.info(
        "Serviços inicializados."
    )

# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application: Application,
):
    """
    Executado durante o encerramento do Application.
    """

    logger.info(
        "Encerrando serviços..."
    )

    await parar_worker()

    logger.info(
        "Serviços encerrados."
    )


# ============================================================
# CRIAR APLICAÇÃO TELEGRAM
# ============================================================

def criar_aplicacao() -> Application:

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --------------------------------------------------------
    # COMANDOS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            comando_start,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            comando_status,
        )
    )

    application.add_handler(
        CommandHandler(
            "fila",
            comando_fila,
        )
    )

    application.add_handler(
        CommandHandler(
            "erros",
            comando_erros,
        )
    )

    application.add_handler(
        CommandHandler(
            "retry",
            comando_retry,
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            comando_stop,
        )
    )

    application.add_handler(
        CommandHandler(
            "iniciar",
            comando_iniciar,
        )
    )

    # --------------------------------------------------------
    # BOTÕES INLINE
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_controle
        )
    )

    # --------------------------------------------------------
    # RECEBER LINKS
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    logger.info(
        "Handlers do Telegram registrados."
    )

    return application

# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "🦊 RAPOSA CAÇADORA"
    )

    logger.info(
        "Iniciando aplicação..."
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # VALIDAR CONFIGURAÇÃO
    # --------------------------------------------------------

    validar_configuracao()

    # --------------------------------------------------------
    # SUPABASE
    # --------------------------------------------------------

    iniciar_supabase()

    # --------------------------------------------------------
    # SERVIDOR HTTP
    # --------------------------------------------------------

    thread_http = threading.Thread(
        target=iniciar_servidor_http,
        name="http-server",
        daemon=True,
    )

    thread_http.start()

    logger.info(
        "Servidor HTTP executando em thread separada."
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    application = criar_aplicacao()

    logger.info(
        "Iniciando Telegram..."
    )

    try:

        await application.initialize()

        await application.start()

        # ----------------------------------------------------
        # INICIAR RECEBIMENTO DE UPDATES
        # ----------------------------------------------------

        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES
        )

        logger.info(
            "Telegram iniciado com sucesso."
        )

        logger.info(
            "Bot operacional."
        )

        # ----------------------------------------------------
        # INICIAR WORKER
        # ----------------------------------------------------

        await iniciar_worker(
            application
        )

        logger.info(
            "Worker de publicação iniciado pelo main."
        )

        # ----------------------------------------------------
        # MANTER PROCESSO VIVO
        # ----------------------------------------------------

        while True:

            await asyncio.sleep(
                3600
            )

    except asyncio.CancelledError:

        logger.info(
            "Aplicação cancelada."
        )

        raise

    except KeyboardInterrupt:

        logger.info(
            "Aplicação interrompida pelo teclado."
        )

    except Exception as erro:

        logger.exception(
            "Erro fatal na aplicação: %s",
            erro,
        )

        raise

    finally:

        logger.info(
            "Iniciando encerramento..."
        )

        try:

            if application.updater:

                await application.updater.stop()

        except Exception as erro:

            logger.warning(
                "Erro ao parar updater: %s",
                erro,
            )

        try:

            if application.running:

                await application.stop()

        except Exception as erro:

            logger.warning(
                "Erro ao parar Application: %s",
                erro,
            )

        try:

            await application.shutdown()

        except Exception as erro:

            logger.warning(
                "Erro ao finalizar Application: %s",
                erro,
            )

        logger.info(
            "Aplicação encerrada."
        )


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Processo interrompido."
        )

    except Exception as erro:

        logger.exception(
            "Falha ao iniciar o sistema: %s",
            erro,
        )

        raise
