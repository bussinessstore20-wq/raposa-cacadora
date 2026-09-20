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

import mercadolivre_oauth


# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

INTERVALO_MINUTOS = int(
    os.getenv("INTERVALO_MINUTOS", "20")
)

PORT = int(
    os.getenv("PORT", "10000")
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

logger = logging.getLogger("raposa-cacadora")


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
        "Mercado Livre OAuth conectado ao Supabase."
    )

    logger.info(
        "Supabase conectado."
    )


# ============================================================
# CONTROLE
# ============================================================

bot_ativo = True

worker_task: asyncio.Task | None = None


# ============================================================
# SERVIDOR HTTP
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        try:

            caminho = self.path

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
                    200,
                    "text/plain; charset=utf-8",
                    "Raposa Cacadora OK",
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
                    status,
                    headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body,
                    headers,
                )

                return

            # ------------------------------------------------
            # CALLBACK
            # ------------------------------------------------

            if rota == "/mercadolivre/callback":

                status, headers, body = (
                    mercadolivre_oauth.oauth_callback_response(
                        query_string
                    )
                )

                self._responder(
                    status,
                    headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body,
                    headers,
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
                    status,
                    headers.get(
                        "Content-Type",
                        "text/html; charset=utf-8",
                    ),
                    body,
                    headers,
                )

                return

            # ------------------------------------------------
            # 404
            # ------------------------------------------------

            self._responder(
                404,
                "text/plain; charset=utf-8",
                "Not Found",
            )

        except Exception as erro:

            logger.exception(
                "Erro no servidor HTTP: %s",
                erro,
            )

            try:

                self._responder(
                    500,
                    "text/plain; charset=utf-8",
                    "Internal Server Error",
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

        corpo = body.encode("utf-8")

        self.send_header(
            "Content-Length",
            str(len(corpo)),
        )

        self.end_headers()

        self.wfile.write(corpo)

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
            "MERCADOLIVRE_REDIRECT_URI inválido."
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

        int(TELEGRAM_ADMIN_ID)

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
# EXTRAIR LINKS
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

        elif (
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
# INSERIR LINKS
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

            mensagem = str(erro)
            texto = mensagem.lower()

            if (
                "duplicate" in texto
                or "unique" in texto
                or "23505" in mensagem
            ):

                duplicados += 1

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
# BUSCAR PRÓXIMO
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
# MARCAR PROCESSANDO
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
# MARCAR PUBLICADO
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
            produto.get("productName")
            or "Produto"
        ),
        "shop_id": produto.get("shopId"),
        "item_id": produto.get("itemId"),
        "image_url": produto.get("imageUrl"),
        "telegram_message_id": telegram_message_id,
        "telegram_chat_id": TELEGRAM_CHAT_ID,
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
# MARCAR ERRO
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
# RECUPERAR PROCESSAMENTOS PRESOS
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

        texto = str(valor).strip()

        if not texto:
            return padrao

        return float(
            texto.replace(",", ".")
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

def moeda(valor):

    valor = numero(valor)

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

def formatar_vendas(vendas):

    vendas = inteiro(vendas)

    return (
        f"{vendas:,}"
        .replace(",", ".")
    )


# ============================================================
# MENSAGEM
# ============================================================

def montar_mensagem(produto):

    nome = (
        produto.get("productName")
        or "Produto"
    )

    preco = numero(
        produto.get("price")
    )

    preco_min = numero(
        produto.get("priceMin")
    )

    desconto = numero(
        produto.get("priceDiscountRate")
    )

    avaliacao = numero(
        produto.get("ratingStar")
    )

    vendas = inteiro(
        produto.get("sales")
    )

    loja = (
        produto.get("shopName")
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

    avaliacao_texto = (
        f"{avaliacao:.1f}"
        if avaliacao > 0
        else "N/D"
    )

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
# PUBLICAR
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str,
):

    mensagem = montar_mensagem(produto)

    image_url = (
        produto.get("imageUrl")
        or ""
    )

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 COMPRAR AGORA",
                    url=link_afiliado,
                )
            ]
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
# NOTIFICAR ADMIN
# ============================================================

async def enviar_notificacao_admin(
    bot: Bot,
    texto: str,
):

    try:

        await bot.send_message(
            chat_id=int(TELEGRAM_ADMIN_ID),
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
        "Processando produto ID %s",
        produto_id,
    )

    reservado = await asyncio.to_thread(
        marcar_processando,
        produto_id,
    )

    if not reservado:

        return False

    try:

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

        sucesso, message_id = (
            await publicar_produto(
                bot,
                produto,
                link,
            )
        )

        if not sucesso:

            raise RuntimeError(
                "Falha ao publicar no Telegram."
            )

        await asyncio.to_thread(
            marcar_publicado,
            produto_id,
            produto,
            message_id,
        )

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
            "Erro Mercado Livre: %s",
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
                "❌ <b>ERRO NO MERCADO LIVRE</b>\n\n"
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
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False


# ============================================================
# TECLADO
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
            ]
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
        "🦊 <b>RAPOSA CAÇADORA</b>\n\n"
        "Envie um ou vários links do "
        "<b>Mercado Livre</b> para colocar na fila.\n\n"
        f"📦 Máximo por envio: <b>{MAX_LINKS_POR_ENVIO}</b>\n"
        f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n\n"
        "Use os botões para controlar a publicação.\n\n"
        "<b>Comandos:</b>\n"
        "/start\n"
        "/status\n"
        "/fila\n"
        "/erros\n"
        "/retry\n"
        "/retry ID"
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

        registros = resposta.data or []

        total = len(registros)

        publicados = sum(
            1
            for item in registros
            if item.get("status") == "published"
        )

        pendentes = sum(
            1
            for item in registros
            if item.get("status") == "pending"
        )

        processando = sum(
            1
            for item in registros
            if item.get("status") == "processing"
        )

        erros = sum(
            1
            for item in registros
            if item.get("status") == "error"
        )

        estado = (
            "🟢 ATIVO"
            if bot_ativo
            else "🔴 PARADO"
        )

        worker_estado = (
            "🟢 EXECUTANDO"
            if worker_task is not None
            and not worker_task.done()
            else "🔴 PARADO"
        )

        mensagem = (
            "🦊 <b>RAPOSA CAÇADORA</b>\n\n"
            "📊 <b>STATUS DA FILA</b>\n\n"
            f"🤖 Bot: <b>{estado}</b>\n"
            f"⚙️ Worker: <b>{worker_estado}</b>\n"
            f"📦 Total: <b>{total}</b>\n"
            f"✅ Publicados: <b>{publicados}</b>\n"
            f"⏳ Aguardando: <b>{pendentes}</b>\n"
            f"🔄 Processando: <b>{processando}</b>\n"
            f"❌ Erros: <b>{erros}</b>\n\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no /status"
        )

        await update.message.reply_text(
            f"❌ Erro:\n{erro}"
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

        registros = resposta.data or []

        if not registros:

            await update.message.reply_text(
                "🦊 A fila está vazia."
            )

            return

        simbolos = {
            "pending": "⏳",
            "processing": "🔄",
            "published": "✅",
            "error": "❌",
        }

        linhas = [
            "🦊 <b>FILA DE PRODUTOS</b>",
            "",
        ]

        for item in registros:

            simbolo = simbolos.get(
                item.get("status"),
                "❓",
            )

            nome = (
                item.get("product_name")
                or "Aguardando processamento"
            )

            if len(nome) > 45:

                nome = nome[:42] + "..."

            linhas.append(
                f"{simbolo} #{item['id']} — {nome}"
            )

        texto = "\n".join(linhas)

        if len(texto) > 4000:

            texto = texto[:3950] + "\n\n..."

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no /fila"
        )

        await update.message.reply_text(
            f"❌ Erro:\n{erro}"
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
                "id,link,erro,tentativas,product_name"
            )
            .eq("status", "error")
            .order("id", desc=False)
            .limit(20)
            .execute()
        )

        registros = resposta.data or []

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

                erro = erro[:297] + "..."

            nome = (
                item.get("product_name")
                or "Produto"
            )

            if len(nome) > 50:

                nome = nome[:47] + "..."

            tentativas = item.get(
                "tentativas",
                0,
            )

            linhas.append(
                f"❌ <b>#{item['id']}</b> — {nome}"
            )

            linhas.append(
                f"   🔁 Tentativas: {tentativas}"
            )

            linhas.append(
                f"   ⚠️ {erro}"
            )

            linhas.append("")

        linhas.append(
            "Use <code>/retry</code> para tentar todos."
        )

        linhas.append(
            "Use <code>/retry ID</code> para tentar um específico."
        )

        texto = "\n".join(linhas)

        if len(texto) > 4000:

            texto = texto[:3950] + "\n\n..."

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no /erros"
        )

        await update.message.reply_text(
            f"❌ Erro:\n{erro}"
        )


# ============================================================
# RETRY TODOS
# ============================================================

async def retry_todos(
    update: Update,
):

    if supabase is None:
        return

    resposta = (
        supabase
        .table("produtos_fila")
        .select("id")
        .eq("status", "error")
        .execute()
    )

    registros = resposta.data or []

    if not registros:

        await update.message.reply_text(
            "✅ Não existem produtos com erro para recuperar."
        )

        return

    recuperados = 0
    falhas = 0

    for item in registros:

        produto_id = item.get("id")

        if not produto_id:
            continue

        try:

            resultado = (
                supabase
                .table("produtos_fila")
                .update(
                    {
                        "status": "pending",
                        "processing_at": None,
                        "erro": None,
                    }
                )
                .eq("id", produto_id)
                .eq("status", "error")
                .execute()
            )

            if resultado.data:

                recuperados += 1

                logger.info(
                    "Produto #%s voltou para pending.",
                    produto_id,
                )

            else:

                falhas += 1

        except Exception as erro:

            falhas += 1

            logger.exception(
                "Falha no retry #%s: %s",
                produto_id,
                erro,
            )

    await update.message.reply_text(
        (
            "🔄 <b>RETRY EXECUTADO</b>\n\n"
            f"♻️ Recuperados: <b>{recuperados}</b>\n"
            f"❌ Falhas: <b>{falhas}</b>\n\n"
            "Os produtos voltaram para <b>pending</b>."
        ),
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# RETRY UM ID
# ============================================================

async def retry_um(
    update: Update,
    produto_id: int,
):

    if supabase is None:
        return

    resposta = (
        supabase
        .table("produtos_fila")
        .select(
            "id,product_name,status,tentativas"
        )
        .eq("id", produto_id)
        .limit(1)
        .execute()
    )

    if not resposta.data:

        await update.message.reply_text(
            f"❌ Produto #{produto_id} não encontrado."
        )

        return

    produto = resposta.data[0]

    if produto.get("status") != "error":

        await update.message.reply_text(
            (
                f"ℹ️ O produto <b>#{produto_id}</b> "
                "não está com status de erro.\n\n"
                f"📦 {produto.get('product_name') or 'Produto'}\n"
                f"📊 Status: <b>{produto.get('status')}</b>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    resultado = (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "pending",
                "processing_at": None,
                "erro": None,
            }
        )
        .eq("id", produto_id)
        .eq("status", "error")
        .execute()
    )

    if not resultado.data:

        await update.message.reply_text(
            f"❌ Não foi possível recuperar #{produto_id}."
        )

        return

    logger.info(
        "Produto #%s recuperado via /retry.",
        produto_id,
    )

    await update.message.reply_text(
        (
            "✅ <b>PRODUTO RECUPERADO</b>\n\n"
            f"🆔 Fila: <b>#{produto_id}</b>\n"
            f"📦 {produto.get('product_name') or 'Produto'}\n"
            "🔄 Status: <b>pending</b>\n\n"
            "O worker poderá processá-lo novamente."
        ),
        parse_mode=ParseMode.HTML,
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

    if update.message is None:
        return

    try:

        # ----------------------------------------------------
        # /retry
        # ----------------------------------------------------

        if not context.args:

            await retry_todos(update)

            return

        # ----------------------------------------------------
        # /retry ID
        # ----------------------------------------------------

        try:

            produto_id = int(
                context.args[0]
            )

        except ValueError:

            await update.message.reply_text(
                (
                    "❌ <b>ID inválido.</b>\n\n"
                    "Use:\n"
                    "• <code>/retry</code> para recuperar todos os erros\n"
                    "• <code>/retry 123</code> para recuperar somente o produto #123"
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        await retry_produto(
            update,
            produto_id,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /retry: %s",
            erro,
        )

        await update.message.reply_text(
            (
                "❌ <b>Erro ao executar /retry</b>\n\n"
                f"<code>{str(erro)[:3000]}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# RETRY - TODOS OS PRODUTOS COM ERRO
# ============================================================

async def retry_todos(
    update: Update,
):

    if supabase is None:

        await update.message.reply_text(
            "❌ Supabase não inicializado."
        )

        return

    try:

        # ----------------------------------------------------
        # Buscar produtos com erro
        # ----------------------------------------------------

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,product_name,tentativas"
            )
            .eq(
                "status",
                "error",
            )
            .order(
                "id",
                desc=False,
            )
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        # ----------------------------------------------------
        # Nenhum erro
        # ----------------------------------------------------

        if not registros:

            await update.message.reply_text(
                (
                    "✅ <b>NENHUM ERRO ENCONTRADO</b>\n\n"
                    "Não existem produtos com status "
                    "<code>error</code> para tentar novamente."
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        recuperados = 0
        falhas = 0

        ids_recuperados = []
        ids_falhos = []

        # ----------------------------------------------------
        # Recuperar cada produto
        # ----------------------------------------------------

        for item in registros:

            produto_id = item.get(
                "id"
            )

            if not produto_id:
                continue

            try:

                resultado = (
                    supabase
                    .table("produtos_fila")
                    .update(
                        {
                            "status": "pending",
                            "processing_at": None,
                            "erro": None,
                        }
                    )
                    .eq(
                        "id",
                        produto_id,
                    )
                    .eq(
                        "status",
                        "error",
                    )
                    .execute()
                )

                if resultado.data:

                    recuperados += 1

                    ids_recuperados.append(
                        produto_id
                    )

                    logger.info(
                        "Retry: produto #%s voltou para pending.",
                        produto_id,
                    )

                else:

                    falhas += 1

                    ids_falhos.append(
                        produto_id
                    )

            except Exception as erro:

                falhas += 1

                ids_falhos.append(
                    produto_id
                )

                logger.exception(
                    "Erro ao recuperar produto #%s: %s",
                    produto_id,
                    erro,
                )

        # ----------------------------------------------------
        # Montar resposta
        # ----------------------------------------------------

        linhas = [
            "🔄 <b>RETRY EXECUTADO</b>",
            "",
            f"📦 Encontrados com erro: <b>{len(registros)}</b>",
            f"✅ Recuperados: <b>{recuperados}</b>",
            f"❌ Falhas: <b>{falhas}</b>",
            "",
        ]

        if ids_recuperados:

            linhas.append(
                "♻️ <b>Produtos recuperados:</b>"
            )

            for produto_id in ids_recuperados[:50]:

                linhas.append(
                    f"• #{produto_id}"
                )

        if ids_falhos:

            linhas.append("")
            linhas.append(
                "⚠️ <b>Falhas:</b>"
            )

            for produto_id in ids_falhos[:50]:

                linhas.append(
                    f"• #{produto_id}"
                )

        linhas.append("")
        linhas.append(
            "⏳ Os produtos recuperados estão "
            "novamente em <b>pending</b>."
        )
        linhas.append(
            "🤖 O worker irá processá-los novamente."
        )

        mensagem = "\n".join(
            linhas
        )

        # ----------------------------------------------------
        # Limite do Telegram
        # ----------------------------------------------------

        if len(mensagem) > 4000:

            mensagem = (
                mensagem[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro em retry_todos: %s",
            erro,
        )

        await update.message.reply_text(
            (
                "❌ <b>Erro ao recuperar produtos.</b>\n\n"
                f"<code>{str(erro)[:3000]}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# RETRY - PRODUTO ESPECÍFICO
# ============================================================

async def retry_produto(
    update: Update,
    produto_id: int,
):

    if supabase is None:

        await update.message.reply_text(
            "❌ Supabase não inicializado."
        )

        return

    try:

        # ----------------------------------------------------
        # Buscar produto
        # ----------------------------------------------------

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,product_name,status,tentativas,erro"
            )
            .eq(
                "id",
                produto_id,
            )
            .limit(1)
            .execute()
        )

        if not resposta.data:

            await update.message.reply_text(
                (
                    f"❌ Produto <b>#{produto_id}</b> "
                    "não encontrado na fila."
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        produto = resposta.data[0]

        status_atual = (
            produto.get(
                "status"
            )
        )

        nome = (
            produto.get(
                "product_name"
            )
            or "Produto aguardando processamento"
        )

        # ----------------------------------------------------
        # Verificar status
        # ----------------------------------------------------

        if status_atual != "error":

            await update.message.reply_text(
                (
                    "ℹ️ <b>PRODUTO NÃO ESTÁ COM ERRO</b>\n"
                    "\n"
                    f"🆔 Fila: <b>#{produto_id}</b>\n"
                    f"📦 {nome}\n"
                    f"📊 Status atual: <b>{status_atual}</b>"
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        # ----------------------------------------------------
        # Recuperar produto
        # ----------------------------------------------------

        resultado = (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "processing_at": None,
                    "erro": None,
                }
            )
            .eq(
                "id",
                produto_id,
            )
            .eq(
                "status",
                "error",
            )
            .execute()
        )

        if not resultado.data:

            await update.message.reply_text(
                (
                    f"❌ Não foi possível recuperar "
                    f"o produto <b>#{produto_id}</b>."
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        logger.info(
            "Produto #%s recuperado via /retry.",
            produto_id,
        )

        # ----------------------------------------------------
        # Resposta
        # ----------------------------------------------------

        await update.message.reply_text(
            (
                "✅ <b>PRODUTO RECUPERADO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"📦 {nome}\n"
                "📊 Status: <b>pending</b>\n"
                f"🔁 Tentativas anteriores: "
                f"<b>{produto.get('tentativas', 0)}</b>\n"
                "\n"
                "🤖 O worker irá tentar processar "
                "este produto novamente."
            ),
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro em retry_produto #%s: %s",
            produto_id,
            erro,
        )

        await update.message.reply_text(
            (
                "❌ <b>Erro no retry do produto.</b>\n\n"
                f"<code>{str(erro)[:3000]}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


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
# CALLBACK DOS BOTÕES
# ============================================================

async def callback_controle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo
    global worker_task

    query = update.callback_query

    if query is None:
        return

    await query.answer()

    # --------------------------------------------------------
    # Verificar autorização
    # --------------------------------------------------------

    if not usuario_autorizado(update):

        await query.answer(
            "⛔ Acesso não autorizado.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # INICIAR
    # --------------------------------------------------------

    if query.data == "bot_iniciar":

        bot_ativo = True

        # Criar worker se não estiver executando
        if (
            worker_task is None
            or worker_task.done()
        ):

            worker_task = asyncio.create_task(
                worker_fila(
                    context.application.bot
                )
            )

            logger.info(
                "Worker criado pelo botão INICIAR."
            )

        texto = (
            "🟢 <b>BOT ATIVADO</b>\n\n"
            "O worker está ativo e continuará "
            "processando a fila."
        )

        try:

            await query.edit_message_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        except Exception:

            await query.message.reply_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        return

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    if query.data == "bot_stop":

        bot_ativo = False

        logger.info(
            "Bot colocado em STOP pelo administrador."
        )

        texto = (
            "🔴 <b>BOT PARADO</b>\n\n"
            "O worker não processará novos produtos "
            "enquanto estiver parado.\n\n"
            "Os produtos já salvos no Supabase "
            "continuam na fila."
        )

        try:

            await query.edit_message_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        except Exception:

            await query.message.reply_text(
                texto,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado_controle(),
            )

        return


# ============================================================
# WORKER DA FILA
# ============================================================

async def worker_fila(
    bot: Bot,
):

    global bot_ativo

    logger.info(
        "Worker da fila iniciado."
    )

    while True:

        try:

            # ------------------------------------------------
            # Recuperar processamentos presos
            # ------------------------------------------------

            await asyncio.to_thread(
                recuperar_processamentos_presos
            )

            # ------------------------------------------------
            # Se estiver parado
            # ------------------------------------------------

            if not bot_ativo:

                await asyncio.sleep(
                    5
                )

                continue

            # ------------------------------------------------
            # Buscar próximo produto
            # ------------------------------------------------

            produto = await asyncio.to_thread(
                buscar_proximo_produto
            )

            if not produto:

                await asyncio.sleep(
                    10
                )

                continue

            # ------------------------------------------------
            # Processar
            # ------------------------------------------------

            sucesso = await processar_produto(
                bot=bot,
                produto_fila=produto,
            )

            # ------------------------------------------------
            # Intervalo após processamento
            # ------------------------------------------------

            if sucesso:

                logger.info(
                    "Produto publicado."
                )

                logger.info(
                    "Aguardando %d minutos para o próximo.",
                    INTERVALO_MINUTOS,
                )

                await asyncio.sleep(
                    INTERVALO_MINUTOS * 60
                )

            else:

                # Em caso de erro, espera um pouco antes
                # de pegar outro produto.
                await asyncio.sleep(
                    10
                )

        except asyncio.CancelledError:

            logger.info(
                "Worker da fila cancelado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "Erro inesperado no worker: %s",
                erro,
            )

            await asyncio.sleep(
                10
            )


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
    )

    links = extrair_links(
        texto
    )

    if not links:

        await update.message.reply_text(
            (
                "❌ Nenhum link válido do "
                "<b>Mercado Livre</b> foi encontrado."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Limite
    # --------------------------------------------------------

    if len(links) > MAX_LINKS_POR_ENVIO:

        await update.message.reply_text(
            (
                f"⚠️ Você enviou <b>{len(links)}</b> links.\n\n"
                f"O máximo permitido por envio é "
                f"<b>{MAX_LINKS_POR_ENVIO}</b>.\n\n"
                "Envie os links novamente respeitando o limite."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    try:

        adicionados, duplicados, erros = (
            await asyncio.to_thread(
                inserir_links,
                links,
            )
        )

        mensagem = (
            "🦊 <b>LINKS PROCESSADOS</b>\n"
            "\n"
            f"📥 Recebidos: <b>{len(links)}</b>\n"
            f"✅ Adicionados: <b>{adicionados}</b>\n"
            f"♻️ Duplicados: <b>{duplicados}</b>\n"
            f"❌ Erros: <b>{len(erros)}</b>\n"
            "\n"
            "Os links adicionados foram colocados "
            "na fila do Supabase."
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao inserir links: %s",
            erro,
        )

        await update.message.reply_text(
            (
                "❌ <b>Erro ao adicionar links.</b>\n\n"
                f"<code>{str(erro)[:3000]}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# MAIN
# ============================================================

def main():

    global worker_task

    logger.info(
        "=========================================="
    )

    logger.info(
        "INICIANDO RAPOSA CAÇADORA"
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # Configuração
    # --------------------------------------------------------

    validar_configuracao()

    iniciar_supabase()

    # --------------------------------------------------------
    # Servidor HTTP
    # --------------------------------------------------------

    thread_http = threading.Thread(
        target=iniciar_servidor_http,
        daemon=True,
        name="http-server",
    )

    thread_http.start()

    # --------------------------------------------------------
    # Criar aplicação Telegram
    # --------------------------------------------------------

    application = (
        Application
        .builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # Comandos
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

    # --------------------------------------------------------
    # Botões
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_controle,
            pattern=r"^bot_(iniciar|stop)$",
        )
    )

    # --------------------------------------------------------
    # Mensagens com links
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    logger.info(
        "Handlers registrados."
    )

    # --------------------------------------------------------
    # Worker inicial
    # --------------------------------------------------------

    async def pos_init(
        app: Application,
    ):

        global worker_task

        if (
            worker_task is None
            or worker_task.done()
        ):

            worker_task = asyncio.create_task(
                worker_fila(
                    app.bot
                )
            )

            logger.info(
                "Worker criado."
            )

    application.post_init = pos_init

    # --------------------------------------------------------
    # Polling
    # --------------------------------------------------------

    logger.info(
        "Iniciando polling do Telegram..."
    )

    application.run_polling(
        drop_pending_updates=False,
    )


# ============================================================
# EXECUÇÃO
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

        # /retry 123
