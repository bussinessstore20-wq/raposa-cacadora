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

    logger.info(
        "Supabase conectado."
    )


# ============================================================
# CONTROLE DO BOT
# ============================================================

bot_ativo = True

worker_task: asyncio.Task | None = None


# ============================================================
# SERVIDOR HTTP PARA O RENDER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"Raposa Cacadora OK"
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

        servidor.serve_forever()

    except Exception as erro:

        logger.exception(
            "Erro no servidor HTTP: %s",
            erro,
        )


# ============================================================
# CONFIGURAÇÃO
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

    if INTERVALO_MINUTOS < 1:
        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0."
        )

    if erros:

        for erro in erros:
            logger.error(erro)

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

        # ----------------------------------------------------
        # LINKS CURTOS DE AFILIADO
        # ----------------------------------------------------

        if "meli.la/" in link_lower:

            links.append(link)
            continue

        # ----------------------------------------------------
        # LINKS DO MERCADO LIVRE
        # ----------------------------------------------------

        if (
            "mercadolivre.com.br" in link_lower
            or "mercadolibre.com" in link_lower
        ):

            links.append(link)

    # --------------------------------------------------------
    # REMOVER DUPLICADOS
    # --------------------------------------------------------

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
                    "Link já existente: %s",
                    link,
                )

            else:

                logger.exception(
                    "Erro ao inserir link: %s",
                    link,
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

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    preco_atual = preco

    if preco_min > 0:

        preco_atual = preco_min

    # --------------------------------------------------------
    # PREÇO ANTERIOR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # AVALIAÇÃO
    # --------------------------------------------------------

    if avaliacao > 0:

        avaliacao_texto = (
            f"{avaliacao:.1f}"
        )

    else:

        avaliacao_texto = (
            "N/D"
        )

    # --------------------------------------------------------
    # MENSAGEM
    # --------------------------------------------------------

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

    # ========================================================
    # TENTAR ENVIAR COM IMAGEM
    # ========================================================

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

    # ========================================================
    # FALLBACK PARA TEXTO
    # ========================================================

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
            chat_id=int(
                TELEGRAM_ADMIN_ID
            ),
            text=texto,
            parse_mode=ParseMode.HTML,
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

    logger.info(
        "Link: %s",
        link,
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
        # BUSCAR MERCADO LIVRE
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
                f"🔗 {link}\n"
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
                f"🔗 {link}\n"
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
        "Exemplo de link de afiliado:\n"
        "<code>https://meli.la/12w2nSd</code>\n"
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

    # --------------------------------------------------------
    # AUTORIZAÇÃO
    # --------------------------------------------------------

    if not usuario_autorizado(update):

        try:
            await query.answer(
                "Você não tem autorização.",
                show_alert=True,
            )
        except Exception:
            pass

        return

    # --------------------------------------------------------
    # INICIAR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # NENHUM LINK
    # --------------------------------------------------------

    if not links:

        await update.message.reply_text(
            (
                "⚠️ <b>NENHUM LINK VÁLIDO</b>\n"
                "\n"
                "Envie um link do Mercado Livre, "
                "por exemplo:\n"
                "\n"
                "<code>https://meli.la/12w2nSd</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # LIMITE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # INSERIR NO SUPABASE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RESPOSTA
    # --------------------------------------------------------

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
        "Worker de publicação iniciado."
    )

    # --------------------------------------------------------
    # PRIMEIRA RECUPERAÇÃO
    # --------------------------------------------------------

    try:

        await asyncio.to_thread(
            recuperar_processamentos_presos
        )

    except Exception as erro:

        logger.exception(
            "Erro ao recuperar processamentos presos: %s",
            erro,
        )

    # --------------------------------------------------------
    # LOOP
    # --------------------------------------------------------

    while True:

        try:

            if not bot_ativo:

                await asyncio.sleep(
                    5
                )

                continue

            # =================================================
            # BUSCAR PRODUTO
            # =================================================

            produto_fila = await asyncio.to_thread(
                buscar_proximo_produto
            )

            if not produto_fila:

                logger.info(
                    "Nenhum produto pendente."
                )

                await asyncio.sleep(
                    30
                )

                continue

            # =================================================
            # PROCESSAR
            # =================================================

            await processar_produto(
                bot=application.bot,
                produto_fila=produto_fila,
            )

            # =================================================
            # INTERVALO
            # =================================================

            logger.info(
                "Aguardando %d minutos "
                "antes do próximo produto.",
                INTERVALO_MINUTOS,
            )

            segundos = (
                INTERVALO_MINUTOS
                * 60
            )

            await asyncio.sleep(
                segundos
            )

        except asyncio.CancelledError:

            logger.info(
                "Worker cancelado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "Erro inesperado no worker: %s",
                erro,
            )

            await asyncio.sleep(
                30
            )


# ============================================================
# INICIAR WORKER
# ============================================================

async def iniciar_worker(
    application: Application,
):

    global worker_task

    if worker_task is not None:

        if not worker_task.done():

            logger.info(
                "Worker já está em execução."
            )

            return

    worker_task = asyncio.create_task(
        worker_publicacao(
            application
        )
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

    worker_task = None


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):

    logger.info(
        "Inicializando aplicação..."
    )

    try:

        iniciar_supabase()

    except Exception as erro:

        logger.exception(
            "Falha ao iniciar Supabase: %s",
            erro,
        )

        raise

    await iniciar_worker(
        application
    )

    logger.info(
        "Aplicação inicializada."
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application: Application,
):

    logger.info(
        "Encerrando aplicação..."
    )

    await parar_worker()

    logger.info(
        "Aplicação encerrada."
    )


# ============================================================
# CRIAR APLICAÇÃO
# ============================================================

def criar_aplicacao():

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ========================================================
    # COMANDOS
    # ========================================================

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

    # ========================================================
    # BOTÕES
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            callback_controle
        )
    )

    # ========================================================
    # MENSAGENS DE TEXTO
    # ========================================================
    #
    # IMPORTANTE:
    #
    # Não usamos filters.COMMAND aqui para não capturar
    # comandos como texto normal.
    #
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    return application


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "INICIANDO RAPOSA CAÇADORA"
    )

    logger.info(
        "Mercado Livre + Telegram + Supabase"
    )

    logger.info(
        "=========================================="
    )

    # ========================================================
    # VALIDAR CONFIGURAÇÃO
    # ========================================================

    validar_configuracao()

    # ========================================================
    # SERVIDOR HTTP
    # ========================================================
    #
    # O Render pode utilizar a porta HTTP para health check.
    #
    # ========================================================

    thread_http = threading.Thread(
        target=iniciar_servidor_http,
        daemon=True,
        name="health-server",
    )

    thread_http.start()

    logger.info(
        "Servidor HTTP iniciado."
    )

    # ========================================================
    # APLICAÇÃO TELEGRAM
    # ========================================================

    application = criar_aplicacao()

    # ========================================================
    # POLLING
    # ========================================================

    logger.info(
        "Iniciando Telegram polling..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


# ============================================================
# EXECUTAR
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        logger.info(
            "Bot encerrado manualmente."
        )

    except Exception as erro:

        logger.exception(
            "Erro fatal ao iniciar o bot: %s",
            erro,
        )

        raise
