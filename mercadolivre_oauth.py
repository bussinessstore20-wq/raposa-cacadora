import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

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

# Renova antes do vencimento real.
# 5 minutos evita que uma requisição pegue um token quase expirado.
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

HTTP_TIMEOUT = 30

TOKEN_TABLE = "mercadolivre_oauth_tokens"

STATE_LOCK = threading.Lock()

# state -> timestamp
_OAUTH_STATES: dict[str, float] = {}

# Lock para evitar duas renovações simultâneas do refresh_token.
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
# CONFIGURAÇÃO
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
            _OAUTH_STATES.pop(state, None)


def gerar_state() -> str:
    """
    Gera um state criptograficamente seguro.
    """
    _limpar_states_expirados()

    state = secrets.token_urlsafe(48)

    with STATE_LOCK:
        _OAUTH_STATES[state] = time.time()

    return state


def validar_e_consumir_state(state: str) -> bool:
    """
    Valida o state e o consome.

    O consumo é importante para impedir reutilização do callback.
    """
    if not state:
        return False

    _limpar_states_expirados()

    with STATE_LOCK:
        criado_em = _OAUTH_STATES.get(state)

        if criado_em is None:
            return False

        if time.time() - criado_em > STATE_EXPIRATION_SECONDS:
            _OAUTH_STATES.pop(state, None)
            return False

        # State de uso único.
        _OAUTH_STATES.pop(state, None)

        return True


# ============================================================
# LOGIN
# ============================================================

def criar_url_autorizacao() -> str:
    """
    Cria a URL para iniciar a autorização no Mercado Livre.
    """
    validar_configuracao_oauth()

    state = gerar_state()

    parametros = {
        "response_type": "code",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "redirect_uri": MERCADOLIVRE_REDIRECT_URI,
        "state": state,
    }

    return (
        f"{MERCADOLIVRE_AUTH_URL}"
        f"?{urlencode(parametros)}"
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
        "Content-Type": "application/x-www-form-urlencoded",
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
            "Não foi possível conectar ao OAuth do Mercado Livre."
        ) from erro

    if response.status_code != 200:
        # Não registrar response.text porque uma resposta de erro
        # pode eventualmente conter informações sensíveis.
        logger.error(
            "OAuth Mercado Livre retornou HTTP %d.",
            response.status_code,
        )

        raise RuntimeError(
            f"Mercado Livre OAuth retornou HTTP "
            f"{response.status_code}."
        )

    try:
        resposta = response.json()

    except ValueError as erro:
        raise RuntimeError(
            "Mercado Livre retornou resposta OAuth inválida."
        ) from erro

    if not isinstance(resposta, dict):
        raise RuntimeError(
            "Resposta OAuth do Mercado Livre não é um objeto JSON."
        )

    return resposta


# ============================================================
# SALVAR TOKEN
# ============================================================

def _salvar_tokens(
    tokens: Dict[str, Any],
) -> None:
    """
    Salva/atualiza os tokens no Supabase.

    O ID fixo 1 transforma a tabela em um armazenamento singleton
    para a conta Mercado Livre conectada ao bot.
    """
    supabase = _obter_supabase()

    access_token = str(
        tokens.get("access_token") or ""
    ).strip()

    refresh_token = str(
        tokens.get("refresh_token") or ""
    ).strip()

    if not access_token:
        raise RuntimeError(
            "Resposta do Mercado Livre não contém access_token."
        )

    if not refresh_token:
        raise RuntimeError(
            "Resposta do Mercado Livre não contém refresh_token."
        )

    expires_in = int(
        tokens.get("expires_in") or 0
    )

    expires_at = int(
        time.time() + expires_in
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
        "token_type": tokens.get("token_type") or "bearer",
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
            "Erro ao salvar token do Mercado Livre no Supabase."
        )

        raise RuntimeError(
            "Não foi possível salvar os tokens no Supabase."
        ) from erro

    logger.info(
        "Tokens do Mercado Livre atualizados no Supabase."
    )


# ============================================================
# RECUPERAR TOKEN
# ============================================================

def _obter_registro_token() -> Optional[Dict[str, Any]]:
    supabase = _obter_supabase()

    try:
        resposta = (
            supabase
            .table(TOKEN_TABLE)
            .select(
                "id,access_token,refresh_token,expires_in,"
                "expires_at,user_id,token_type,scope,updated_at"
            )
            .eq("id", 1)
            .limit(1)
            .execute()
        )

    except Exception as erro:
        logger.exception(
            "Erro ao consultar token do Mercado Livre."
        )

        raise RuntimeError(
            "Não foi possível consultar o token do Mercado Livre."
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
    """
    Renova o access token.

    IMPORTANTE:
    O Mercado Livre usa refresh token rotativo.
    Portanto o novo refresh_token substitui imediatamente o anterior.
    """
    refresh_token = str(
        registro.get("refresh_token") or ""
    ).strip()

    if not refresh_token:
        raise RuntimeError(
            "Não existe refresh_token salvo no Supabase."
        )

    dados = {
        "grant_type": "refresh_token",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "client_secret": MERCADOLIVRE_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    resposta = _post_token(dados)

    novo_access_token = str(
        resposta.get("access_token") or ""
    ).strip()

    novo_refresh_token = str(
        resposta.get("refresh_token") or ""
    ).strip()

    if not novo_access_token:
        raise RuntimeError(
            "Refresh do Mercado Livre não retornou access_token."
        )

    if not novo_refresh_token:
        raise RuntimeError(
            "Refresh do Mercado Livre não retornou novo refresh_token."
        )

    # Salva o NOVO refresh token.
    _salvar_tokens(resposta)

    logger.info(
        "Access token do Mercado Livre renovado com sucesso."
    )

    return novo_access_token


# ============================================================
# OBTER ACCESS TOKEN ATUAL
# ============================================================

def obter_access_token_mercadolivre() -> str:
    """
    Retorna um access_token válido.

    Fluxo:

    1. Busca token no Supabase.
    2. Verifica expiração.
    3. Se ainda estiver válido, retorna.
    4. Se estiver próximo de expirar, executa refresh.
    """
    registro = _obter_registro_token()

    if not registro:
        # Compatibilidade temporária com instalação antiga.
        token_legacy = os.getenv(
            "MERCADOLIVRE_ACCESS_TOKEN",
            "",
        ).strip()

        if token_legacy:
            logger.warning(
                "Usando MERCADOLIVRE_ACCESS_TOKEN legado. "
                "Recomenda-se concluir o OAuth."
            )

            return token_legacy

        raise RuntimeError(
            "Mercado Livre não está conectado. "
            "Acesse /mercadolivre/login."
        )

    access_token = str(
        registro.get("access_token") or ""
    ).strip()

    expires_at = int(
        registro.get("expires_at") or 0
    )

    agora = int(
        time.time()
    )

    if access_token and (
        expires_at > agora + TOKEN_REFRESH_MARGIN_SECONDS
    ):
        return access_token

    # Apenas uma thread pode usar o refresh_token por vez.
    with _REFRESH_LOCK:

        # Outra thread pode ter renovado enquanto esperávamos.
        registro_atualizado = _obter_registro_token()

        if not registro_atualizado:
            raise RuntimeError(
                "Token do Mercado Livre desapareceu do Supabase."
            )

        access_token_atualizado = str(
            registro_atualizado.get("access_token") or ""
        ).strip()

        expires_at_atualizado = int(
            registro_atualizado.get("expires_at") or 0
        )

        agora = int(
            time.time()
        )

        if access_token_atualizado and (
            expires_at_atualizado
            > agora + TOKEN_REFRESH_MARGIN_SECONDS
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
    Valida state e troca o authorization code por tokens.
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

    if not validar_e_consumir_state(state):
        raise ValueError(
            "State inválido, expirado ou já utilizado."
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

    # Não devolvemos access_token ou refresh_token.
    return {
        "user_id": tokens.get("user_id"),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "token_type": tokens.get("token_type"),
    }


# ============================================================
# STATUS
# ============================================================

def obter_status_mercadolivre() -> Dict[str, Any]:
    """
    Retorna somente informações seguras.
    """
    registro = _obter_registro_token()

    if not registro:
        return {
            "conectado": False,
            "user_id": None,
            "expira_em": None,
        }

    expires_at = int(
        registro.get("expires_at") or 0
    )

    agora = int(
        time.time()
    )

    segundos_restantes = max(
        0,
        expires_at - agora,
    )

    conectado = bool(
        registro.get("access_token")
        and registro.get("refresh_token")
    )

    return {
        "conectado": conectado,
        "user_id": registro.get("user_id"),
        "expira_em": (
            datetime.fromtimestamp(
                expires_at,
                tz=timezone.utc,
            ).isoformat()
            if expires_at
            else None
        ),
        "segundos_restantes": segundos_restantes,
        "precisa_refresh": (
            segundos_restantes
            <= TOKEN_REFRESH_MARGIN_SECONDS
        ),
        "updated_at": registro.get(
            "updated_at"
        ),
    }
