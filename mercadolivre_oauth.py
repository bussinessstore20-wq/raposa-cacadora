import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode

import requests
from supabase import Client


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

MERCADOLIVRE_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

MERCADOLIVRE_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

MERCADOLIVRE_CLIENT_ID = os.getenv(
    "MERCADOLIVRE_CLIENT_ID",
    "",
).strip()

MERCADOLIVRE_CLIENT_SECRET = os.getenv(
    "MERCADOLIVRE_CLIENT_SECRET",
    "",
).strip()

MERCADOLIVRE_REDIRECT_URI = os.getenv(
    "MERCADOLIVRE_REDIRECT_URI",
    "https://raposa-cacadora.onrender.com/mercadolivre/callback",
).strip()


# ============================================================
# SEGURANÇA
# ============================================================

STATE_EXPIRATION_SECONDS = 10 * 60

TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

HTTP_TIMEOUT = 30

TOKEN_TABLE = "mercadolivre_oauth_tokens"

STATE_LOCK = threading.Lock()

# state -> timestamp
_OAUTH_STATES: dict[str, float] = {}

# Impede dois refresh simultâneos.
_REFRESH_LOCK = threading.Lock()


# ============================================================
# SUPABASE
# ============================================================

_supabase: Optional[Client] = None


def configurar_supabase(client: Client) -> None:
    """
    Recebe o cliente Supabase já inicializado pelo bot.py.
    """
    global _supabase

    _supabase = client

    logger.info(
        "Mercado Livre OAuth conectado ao Supabase."
    )


def _obter_supabase() -> Client:
    if _supabase is None:
        raise RuntimeError(
            "Supabase do Mercado Livre OAuth não foi configurado."
        )

    return _supabase


# ============================================================
# CONFIGURAÇÃO OAUTH
# ============================================================

def validar_configuracao_oauth() -> None:
    erros = []

    if not MERCADOLIVRE_CLIENT_ID:
        erros.append(
            "MERCADOLIVRE_CLIENT_ID não configurado."
        )

    if not MERCADOLIVRE_CLIENT_SECRET:
        erros.append(
            "MERCADOLIVRE_CLIENT_SECRET não configurado."
        )

    if not MERCADOLIVRE_REDIRECT_URI:
        erros.append(
            "MERCADOLIVRE_REDIRECT_URI não configurado."
        )

    if erros:
        raise RuntimeError(
            "Configuração OAuth do Mercado Livre inválida: "
            + " ".join(erros)
        )


# ============================================================
# STATE
# ============================================================

def _limpar_states_expirados() -> None:
    agora = time.time()

    with STATE_LOCK:
        expirados = [
            state
            for state, criado_em in _OAUTH_STATES.items()
            if agora - criado_em > STATE_EXPIRATION_SECONDS
        ]

        for state in expirados:
            _OAUTH_STATES.pop(
                state,
                None,
            )


def gerar_state() -> str:
    """
    Gera um state criptograficamente seguro.
    """
    _limpar_states_expirados()

    state = secrets.token_urlsafe(48)

    with STATE_LOCK:
        _OAUTH_STATES[state] = time.time()

    return state


def validar_e_consumir_state(
    state: str,
) -> bool:
    """
    Valida o state e o consome.

    O consumo impede reutilização do callback.
    """
    if not state:
        return False

    _limpar_states_expirados()

    with STATE_LOCK:
        criado_em = _OAUTH_STATES.get(state)

        if criado_em is None:
            return False

        if (
            time.time() - criado_em
            > STATE_EXPIRATION_SECONDS
        ):
            _OAUTH_STATES.pop(
                state,
                None,
            )

            return False

        # State de uso único.
        _OAUTH_STATES.pop(
            state,
            None,
        )

        return True


# ============================================================
# URL DE AUTORIZAÇÃO
# ============================================================

def criar_url_autorizacao() -> str:

    validar_configuracao_oauth()

    state = gerar_state()

    logger.info(
        "OAuth Mercado Livre: state gerado com sucesso."
    )

    parametros = {
        "response_type": "code",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "redirect_uri": MERCADOLIVRE_REDIRECT_URI,
        "state": state,
    }

    logger.info(
        "OAuth Mercado Livre: redirect_uri=%s",
        MERCADOLIVRE_REDIRECT_URI,
    )

    return (
        f"{MERCADOLIVRE_AUTH_URL}"
        f"?{urlencode(parametros)}"
    )

# ============================================================
# RESPOSTA HTTP - LOGIN
# ============================================================

def oauth_login_response():
    """
    Retorna:

        status,
        headers,
        body

    para o endpoint:

        /mercadolivre/login
    """
    try:
        url = criar_url_autorizacao()

        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Mercado Livre - Autorização</title>
</head>
<body>
    <h1>🦊 Raposa Caçadora</h1>

    <p>
        Clique no botão abaixo para autorizar
        o Mercado Livre.
    </p>

    <p>
        <a href="{url}">
            <button
                style="
                    padding: 12px 20px;
                    font-size: 16px;
                    cursor: pointer;
                "
            >
                Autorizar Mercado Livre
            </button>
        </a>
    </p>
</body>
</html>
"""

        return (
            200,
            {
                "Content-Type": "text/html; charset=utf-8",
            },
            html,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao criar login OAuth."
        )

        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Erro OAuth</title>
</head>
<body>
    <h1>❌ Erro</h1>
    <p>{str(erro)}</p>
</body>
</html>
"""

        return (
            500,
            {
                "Content-Type": "text/html; charset=utf-8",
            },
            html,
        )


# ============================================================
# HTTP TOKEN
# ============================================================

def _post_token(
    dados: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Executa POST no endpoint OAuth do Mercado Livre.
    """
    validar_configuracao_oauth()

    headers = {
        "Accept": "application/json",
        "Content-Type": (
            "application/x-www-form-urlencoded"
        ),
        "User-Agent": "Raposa-Cacadora/1.0",
    }

    try:

        response = requests.post(
            MERCADOLIVRE_TOKEN_URL,
            data=dados,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as erro:

        logger.error(
            "Erro de conexão com OAuth do Mercado Livre: %s",
            erro,
        )

        raise RuntimeError(
            "Não foi possível conectar ao OAuth "
            "do Mercado Livre."
        ) from erro

    if response.status_code != 200:

        logger.error(
            "OAuth Mercado Livre retornou HTTP %d.",
            response.status_code,
        )

        raise RuntimeError(
            "Mercado Livre OAuth retornou HTTP "
            f"{response.status_code}."
        )

    try:

        resposta = response.json()

    except ValueError as erro:

        raise RuntimeError(
            "Mercado Livre retornou resposta OAuth inválida."
        ) from erro

    if not isinstance(
        resposta,
        dict,
    ):

        raise RuntimeError(
            "Resposta OAuth do Mercado Livre "
            "não é um objeto JSON."
        )

    return resposta


# ============================================================
# SALVAR TOKEN
# ============================================================

def _salvar_tokens(
    tokens: Dict[str, Any],
) -> None:
    """
    Salva os tokens no Supabase.

    O ID 1 funciona como registro único da conta
    Mercado Livre conectada.
    """
    supabase = _obter_supabase()

    access_token = str(
        tokens.get("access_token")
        or ""
    ).strip()

    refresh_token = str(
        tokens.get("refresh_token")
        or ""
    ).strip()

    if not access_token:

        raise RuntimeError(
            "Resposta do Mercado Livre não contém "
            "access_token."
        )

    if not refresh_token:

        raise RuntimeError(
            "Resposta do Mercado Livre não contém "
            "refresh_token."
        )

    try:

        expires_in = int(
            tokens.get("expires_in")
            or 0
        )

    except Exception:

        expires_in = 0

    expires_at = int(
        time.time()
        + expires_in
    )

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    dados = {
        "id": 1,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "expires_at": expires_at,
        "user_id": tokens.get("user_id"),
        "token_type": (
            tokens.get("token_type")
            or "bearer"
        ),
        "scope": tokens.get("scope"),
        "updated_at": agora,
    }

    try:

        (
            supabase
            .table(TOKEN_TABLE)
            .upsert(
                dados,
                on_conflict="id",
            )
            .execute()
        )

    except Exception as erro:

        logger.exception(
            "Erro ao salvar token do Mercado Livre "
            "no Supabase."
        )

        raise RuntimeError(
            "Não foi possível salvar os tokens "
            "no Supabase."
        ) from erro

    logger.info(
        "Tokens do Mercado Livre atualizados no Supabase."
    )


# ============================================================
# RECUPERAR TOKEN
# ============================================================

def _obter_registro_token(
) -> Optional[Dict[str, Any]]:

    supabase = _obter_supabase()

    try:

        resposta = (
            supabase
            .table(TOKEN_TABLE)
            .select(
                "id,"
                "access_token,"
                "refresh_token,"
                "expires_in,"
                "expires_at,"
                "user_id,"
                "token_type,"
                "scope,"
                "updated_at"
            )
            .eq(
                "id",
                1,
            )
            .limit(1)
            .execute()
        )

    except Exception as erro:

        logger.exception(
            "Erro ao consultar token "
            "do Mercado Livre."
        )

        raise RuntimeError(
            "Não foi possível consultar o token "
            "do Mercado Livre."
        ) from erro

    if not resposta.data:
        return None

    return resposta.data[0]


# ============================================================
# REFRESH TOKEN
# ============================================================

def _renovar_access_token(
    registro: Dict[str, Any],
) -> str:

    refresh_token = str(
        registro.get("refresh_token")
        or ""
    ).strip()

    if not refresh_token:

        raise RuntimeError(
            "Não existe refresh_token salvo "
            "no Supabase."
        )

    dados = {
        "grant_type": "refresh_token",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "client_secret": MERCADOLIVRE_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    resposta = _post_token(
        dados
    )

    novo_access_token = str(
        resposta.get("access_token")
        or ""
    ).strip()

    if not novo_access_token:

        raise RuntimeError(
            "Refresh do Mercado Livre não retornou "
            "access_token."
        )

    novo_refresh_token = str(
        resposta.get("refresh_token")
        or ""
    ).strip()

    # Alguns fluxos podem não devolver
    # outro refresh_token.
    #
    # Nesse caso preservamos o atual.

    if not novo_refresh_token:

        resposta["refresh_token"] = (
            refresh_token
        )

    _salvar_tokens(
        resposta
    )

    logger.info(
        "Access token do Mercado Livre "
        "renovado com sucesso."
    )

    return novo_access_token

# ============================================================
# OBTER ACCESS TOKEN ATUAL
# ============================================================

def obter_access_token_mercadolivre() -> str:
    """
    Retorna um access_token válido.

    Fluxo:

    1. Busca o token no Supabase.
    2. Verifica a validade.
    3. Se estiver válido, retorna.
    4. Se estiver próximo de expirar, renova.
    """

    registro = _obter_registro_token()

    if not registro:

        token_legacy = os.getenv(
            "MERCADOLIVRE_ACCESS_TOKEN",
            "",
        ).strip()

        if token_legacy:

            logger.warning(
                "Usando MERCADOLIVRE_ACCESS_TOKEN legado."
            )

            return token_legacy

        raise RuntimeError(
            "Mercado Livre não está conectado. "
            "Acesse /mercadolivre/login."
        )

    access_token = str(
        registro.get("access_token")
        or ""
    ).strip()

    try:

        expires_at = int(
            registro.get("expires_at")
            or 0
        )

    except Exception:

        expires_at = 0

    agora = int(
        time.time()
    )

    if access_token and (
        expires_at
        > agora
        + TOKEN_REFRESH_MARGIN_SECONDS
    ):

        return access_token

    with _REFRESH_LOCK:

        registro_atualizado = (
            _obter_registro_token()
        )

        if not registro_atualizado:

            raise RuntimeError(
                "Token do Mercado Livre "
                "desapareceu do Supabase."
            )

        access_token_atualizado = str(
            registro_atualizado.get(
                "access_token"
            )
            or ""
        ).strip()

        try:

            expires_at_atualizado = int(
                registro_atualizado.get(
                    "expires_at"
                )
                or 0
            )

        except Exception:

            expires_at_atualizado = 0

        agora = int(
            time.time()
        )

        if access_token_atualizado and (
            expires_at_atualizado
            > agora
            + TOKEN_REFRESH_MARGIN_SECONDS
        ):

            return access_token_atualizado

        return _renovar_access_token(
            registro_atualizado
        )


# ============================================================
# CALLBACK
# ============================================================

def processar_callback(
    code: str,
    state: str,
) -> Dict[str, Any]:
    """
    Valida o state e troca o authorization code
    por access_token + refresh_token.
    """

    validar_configuracao_oauth()

    if not code:

        raise ValueError(
            "Authorization code não informado."
        )

    if not state:

        raise ValueError(
            "State não informado."
        )

    if not validar_e_consumir_state(
        state
    ):

        raise ValueError(
            "State inválido, expirado "
            "ou já utilizado."
        )

    dados = {
        "grant_type": "authorization_code",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "client_secret": MERCADOLIVRE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": MERCADOLIVRE_REDIRECT_URI,
    }

    tokens = _post_token(
        dados
    )

    _salvar_tokens(
        tokens
    )

    logger.info(
        "Autorização do Mercado Livre concluída."
    )

    return {
        "user_id": tokens.get(
            "user_id"
        ),
        "expires_in": tokens.get(
            "expires_in"
        ),
        "scope": tokens.get(
            "scope"
        ),
        "token_type": tokens.get(
            "token_type"
        ),
    }


# ============================================================
# RESPOSTA HTTP - CALLBACK
# ============================================================

def oauth_callback_response(
    query_string: str,
):
    """
    Processa:

        /mercadolivre/callback?code=...&state=...

    """

    try:

        parametros = parse_qs(
            query_string,
            keep_blank_values=True,
        )

        code = (
            parametros.get(
                "code",
                [""],
            )[0]
            .strip()
        )

        state = (
            parametros.get(
                "state",
                [""],
            )[0]
            .strip()
        )

        erro_oauth = (
            parametros.get(
                "error",
                [""],
            )[0]
            .strip()
        )

        descricao_erro = (
            parametros.get(
                "error_description",
                [""],
            )[0]
            .strip()
        )

        # ----------------------------------------------------
        # USUÁRIO NEGOU A AUTORIZAÇÃO
        # ----------------------------------------------------

        if erro_oauth:

            mensagem_erro = (
                descricao_erro
                or erro_oauth
                or "Autorização cancelada."
            )

            logger.warning(
                "OAuth Mercado Livre cancelado: %s",
                erro_oauth,
            )

            html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Autorização cancelada</title>
</head>
<body>
    <h1>⚠️ Autorização não concluída</h1>

    <p>
        O Mercado Livre não autorizou a conexão.
    </p>

    <p>
        {mensagem_erro}
    </p>

    <p>
        Você pode fechar esta página.
    </p>
</body>
</html>
"""

            return (
                400,
                {
                    "Content-Type": (
                        "text/html; charset=utf-8"
                    ),
                },
                html,
            )

        # ----------------------------------------------------
        # FALTANDO CODE
        # ----------------------------------------------------

        if not code:

            raise ValueError(
                "O Mercado Livre não enviou "
                "o authorization code."
            )

        # ----------------------------------------------------
        # FALTANDO STATE
        # ----------------------------------------------------

        if not state:

            raise ValueError(
                "O Mercado Livre não enviou "
                "o parâmetro state."
            )

        # ----------------------------------------------------
        # TROCAR CODE POR TOKEN
        # ----------------------------------------------------

        resultado = processar_callback(
            code=code,
            state=state,
        )

        user_id = (
            resultado.get(
                "user_id"
            )
            or "N/D"
        )

        expires_in = int(
            resultado.get(
                "expires_in"
            )
            or 0
        )

        if expires_in > 0:

            minutos = (
                expires_in // 60
            )

            validade = (
                f"{minutos} minutos"
            )

        else:

            validade = "N/D"

        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Mercado Livre conectado</title>
</head>
<body>
    <h1>✅ Mercado Livre conectado!</h1>

    <p>
        A autorização foi concluída com sucesso.
    </p>

    <p>
        <strong>ID do usuário:</strong>
        {user_id}
    </p>

    <p>
        <strong>Validade inicial:</strong>
        {validade}
    </p>

    <p>
        O token foi salvo com segurança no Supabase.
    </p>

    <p>
        A renovação automática ficará responsável
        por manter a conexão ativa.
    </p>

    <p>
        Você pode fechar esta página.
    </p>
</body>
</html>
"""

        return (
            200,
            {
                "Content-Type": (
                    "text/html; charset=utf-8"
                ),
            },
            html,
        )

    except Exception as erro:

        logger.exception(
            "Erro no callback OAuth do Mercado Livre."
        )

        mensagem = str(
            erro
        )

        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Erro Mercado Livre</title>
</head>
<body>
    <h1>❌ Erro na autorização</h1>

    <p>
        Não foi possível concluir a conexão
        com o Mercado Livre.
    </p>

    <p>
        <strong>Detalhe:</strong>
        {mensagem}
    </p>

    <p>
        Tente iniciar novamente pelo endereço
        de login do Mercado Livre.
    </p>
</body>
</html>
"""

        return (
            500,
            {
                "Content-Type": (
                    "text/html; charset=utf-8"
                ),
            },
            html,
        )


# ============================================================
# STATUS
# ============================================================

def obter_status_mercadolivre() -> Dict[str, Any]:
    """
    Retorna somente informações seguras.
    Nunca retorna access_token ou refresh_token.
    """

    registro = _obter_registro_token()

    if not registro:

        return {
            "conectado": False,
            "user_id": None,
            "expira_em": None,
            "segundos_restantes": 0,
            "precisa_refresh": False,
            "updated_at": None,
        }

    try:

        expires_at = int(
            registro.get(
                "expires_at"
            )
            or 0
        )

    except Exception:

        expires_at = 0

    agora = int(
        time.time()
    )

    segundos_restantes = max(
        0,
        expires_at - agora,
    )

    conectado = bool(
        registro.get(
            "access_token"
        )
        and registro.get(
            "refresh_token"
        )
    )

    return {
        "conectado": conectado,
        "user_id": registro.get(
            "user_id"
        ),
        "expira_em": (
            datetime.fromtimestamp(
                expires_at,
                tz=timezone.utc,
            ).isoformat()
            if expires_at
            else None
        ),
        "segundos_restantes": (
            segundos_restantes
        ),
        "precisa_refresh": (
            segundos_restantes
            <= TOKEN_REFRESH_MARGIN_SECONDS
        ),
        "updated_at": registro.get(
            "updated_at"
        ),
    }


# ============================================================
# RESPOSTA HTTP - STATUS
# ============================================================

def oauth_status_response():
    """
    Responde à rota:

        /mercadolivre/status

    sem expor tokens.
    """

    try:

        status = obter_status_mercadolivre()

        if status["conectado"]:

            usuario = (
                status.get(
                    "user_id"
                )
                or "N/D"
            )

            expira_em = (
                status.get(
                    "expira_em"
                )
                or "N/D"
            )

            segundos = int(
                status.get(
                    "segundos_restantes",
                    0,
                )
                or 0
            )

            horas = (
                segundos // 3600
            )

            minutos = (
                (segundos % 3600)
                // 60
            )

            if status.get(
                "precisa_refresh"
            ):

                situacao = (
                    "🟡 Token próximo da renovação"
                )

            else:

                situacao = (
                    "🟢 Conectado"
                )

            html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Status Mercado Livre</title>
</head>
<body>
    <h1>🦊 Mercado Livre</h1>

    <h2>{situacao}</h2>

    <p>
        <strong>Usuário:</strong>
        {usuario}
    </p>

    <p>
        <strong>Tempo restante aproximado:</strong>
        {horas}h {minutos}min
    </p>

    <p>
        <strong>Expira em:</strong>
        {expira_em}
    </p>

    <p>
        O access token e o refresh token
        não são exibidos nesta página.
    </p>
</body>
</html>
"""

        else:

            html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Status Mercado Livre</title>
</head>
<body>
    <h1>🦊 Mercado Livre</h1>

    <h2>🔴 Não conectado</h2>

    <p>
        Nenhum token OAuth foi encontrado no Supabase.
    </p>

    <p>
        Acesse:
        <a href="/mercadolivre/login">
            /mercadolivre/login
        </a>
    </p>
</body>
</html>
"""

        return (
            200,
            {
                "Content-Type": (
                    "text/html; charset=utf-8"
                ),
            },
            html,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao consultar status OAuth."
        )

        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Erro</title>
</head>
<body>
    <h1>❌ Erro</h1>

    <p>
        {str(erro)}
    </p>
</body>
</html>
"""

        return (
            500,
            {
                "Content-Type": (
                    "text/html; charset=utf-8"
                ),
            },
            html,
        )
