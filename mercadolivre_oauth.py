import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from supabase import Client, create_client


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

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

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()


# ============================================================
# ENDPOINTS OFICIAIS DO MERCADO LIVRE
# ============================================================

OAUTH_AUTHORIZE_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

OAUTH_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)


# ============================================================
# CONFIGURAÇÕES DE SEGURANÇA
# ============================================================

TOKEN_TABLE = "mercadolivre_tokens"

REQUEST_TIMEOUT = 30

# O refresh será realizado quando faltarem 10 minutos
# ou menos para o access_token expirar.
TOKEN_REFRESH_MARGIN_SECONDS = 600

# State OAuth permanece válido por 10 minutos.
STATE_TTL_SECONDS = 600


# ============================================================
# ERRO
# ============================================================

class MercadoLivreOAuthError(Exception):
    """Erro relacionado ao OAuth do Mercado Livre."""


# ============================================================
# CLIENTE SUPABASE
# ============================================================

_supabase: Optional[Client] = None

_supabase_lock = threading.Lock()


def _obter_supabase() -> Client:
    """
    Retorna o cliente Supabase.

    O cliente é criado apenas uma vez por processo.
    """

    global _supabase

    if _supabase is not None:
        return _supabase

    with _supabase_lock:

        if _supabase is not None:
            return _supabase

        if not SUPABASE_URL:
            raise MercadoLivreOAuthError(
                "SUPABASE_URL não configurada."
            )

        if not SUPABASE_KEY:
            raise MercadoLivreOAuthError(
                "SUPABASE_KEY não configurada."
            )

        _supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY,
        )

    return _supabase


# ============================================================
# STATE OAUTH
# ============================================================

_states: Dict[str, float] = {}

_states_lock = threading.Lock()


def gerar_state() -> str:
    """
    Gera um state criptograficamente seguro.

    O state é armazenado temporariamente em memória.
    """

    state = secrets.token_urlsafe(32)

    agora = time.time()

    with _states_lock:

        # Remove states expirados.
        expirados = [
            chave
            for chave, timestamp in _states.items()
            if agora - timestamp > STATE_TTL_SECONDS
        ]

        for chave in expirados:
            _states.pop(
                chave,
                None,
            )

        _states[state] = agora

    return state


def validar_e_consumir_state(
    state: str,
) -> bool:
    """
    Valida o state recebido pelo callback.

    O state é de uso único.
    Depois de validado, é removido da memória.
    """

    if not state:
        return False

    agora = time.time()

    with _states_lock:

        timestamp = _states.get(
            state
        )

        if timestamp is None:
            return False

        if (
            agora - timestamp
            > STATE_TTL_SECONDS
        ):
            _states.pop(
                state,
                None,
            )

            return False

        # State de uso único.
        _states.pop(
            state,
            None,
        )

        return True


# ============================================================
# CONFIGURAÇÃO DO OAUTH
# ============================================================

def validar_configuracao_oauth() -> None:
    """
    Verifica se todas as variáveis necessárias
    para o OAuth estão configuradas.
    """

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

    if not SUPABASE_URL:
        erros.append(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        erros.append(
            "SUPABASE_KEY não configurada."
        )

    if erros:
        raise MercadoLivreOAuthError(
            " | ".join(erros)
        )


# ============================================================
# URL DE AUTORIZAÇÃO
# ============================================================

def obter_url_autorizacao() -> str:
    """
    Gera a URL para iniciar a autorização OAuth.
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
        f"{OAUTH_AUTHORIZE_URL}?"
        f"{urlencode(parametros)}"
    )


# ============================================================
# POST NO ENDPOINT DE TOKEN
# ============================================================

def _post_token(
    dados: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Executa POST no endpoint OAuth do Mercado Livre.

    Nunca registra os dados enviados porque eles podem
    conter client_secret, access_token ou refresh_token.
    """

    try:

        resposta = requests.post(
            OAUTH_TOKEN_URL,
            data=dados,
            headers={
                "Accept": "application/json",
                "Content-Type": (
                    "application/x-www-form-urlencoded"
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )

    except requests.RequestException as erro:

        logger.error(
            "Erro de conexão com o OAuth do Mercado Livre."
        )

        raise MercadoLivreOAuthError(
            "Erro de conexão com o Mercado Livre."
        ) from erro

    if resposta.status_code != 200:

        logger.error(
            "OAuth do Mercado Livre retornou HTTP %s.",
            resposta.status_code,
        )

        # Tentamos obter apenas uma mensagem de erro.
        # Nunca registramos o corpo completo.
        try:

            erro_json = resposta.json()

            mensagem = (
                erro_json.get("error")
                or erro_json.get("message")
                or "erro desconhecido"
            )

        except ValueError:

            mensagem = (
                "resposta inválida"
            )

        raise MercadoLivreOAuthError(
            f"Falha no OAuth do Mercado Livre: {mensagem}"
        )

    try:

        dados_resposta = resposta.json()

    except ValueError as erro:

        raise MercadoLivreOAuthError(
            "Mercado Livre retornou uma resposta OAuth inválida."
        ) from erro

    if not isinstance(
        dados_resposta,
        dict,
    ):
        raise MercadoLivreOAuthError(
            "Resposta OAuth inesperada."
        )

    if not dados_resposta.get(
        "access_token"
    ):
        raise MercadoLivreOAuthError(
            "Resposta OAuth não contém access_token."
        )

    if not dados_resposta.get(
        "refresh_token"
    ):
        raise MercadoLivreOAuthError(
            "Resposta OAuth não contém refresh_token."
        )

    return dados_resposta


# ============================================================
# TROCAR AUTHORIZATION CODE POR TOKENS
# ============================================================

def trocar_code_por_tokens(
    code: str,
) -> Dict[str, Any]:
    """
    Troca o authorization code recebido pelo Mercado Livre
    por access_token e refresh_token.
    """

    if not code:
        raise MercadoLivreOAuthError(
            "Authorization code vazio."
        )

    validar_configuracao_oauth()

    dados = {
        "grant_type": "authorization_code",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "client_secret": MERCADOLIVRE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": MERCADOLIVRE_REDIRECT_URI,
    }

    return _post_token(
        dados
    )


# ============================================================
# CALCULAR DATA DE EXPIRAÇÃO
# ============================================================

def _calcular_expires_at(
    expires_in: Any,
) -> str:
    """
    Converte expires_in em uma data/hora UTC.
    """

    try:
        segundos = int(
            expires_in
        )

    except (
        TypeError,
        ValueError,
    ):
        segundos = 0

    agora = datetime.now(
        timezone.utc
    )

    timestamp = (
        agora.timestamp()
        + max(
            segundos,
            0,
        )
    )

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


# ============================================================
# SALVAR TOKENS NO SUPABASE
# ============================================================

def salvar_tokens(
    token_data: Dict[str, Any],
) -> None:
    """
    Salva os tokens no Supabase.

    O registro usa id=1 porque esta aplicação possui
    uma única autorização do Mercado Livre.

    Se o Mercado Livre fornecer um novo refresh_token,
    ele substitui automaticamente o anterior.
    """

    access_token = token_data.get(
        "access_token"
    )

    refresh_token = token_data.get(
        "refresh_token"
    )

    if not access_token:
        raise MercadoLivreOAuthError(
            "Não é possível salvar token sem access_token."
        )

    if not refresh_token:
        raise MercadoLivreOAuthError(
            "Não é possível salvar token sem refresh_token."
        )

    try:

        expires_in = int(
            token_data.get(
                "expires_in",
                0,
            ) or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        expires_in = 0

    expires_at = _calcular_expires_at(
        expires_in
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

        "user_id": token_data.get(
            "user_id"
        ),

        "token_type": token_data.get(
            "token_type"
        ),

        "scope": token_data.get(
            "scope"
        ),

        "updated_at": agora,
    }

    try:

        (
            _obter_supabase()
            .table(
                TOKEN_TABLE
            )
            .upsert(
                dados,
                on_conflict="id",
            )
            .execute()
        )

    except Exception as erro:

        logger.exception(
            "Erro ao salvar tokens do Mercado Livre no Supabase."
        )

        raise MercadoLivreOAuthError(
            "Não foi possível salvar os tokens no Supabase."
        ) from erro


# ============================================================
# OBTER TOKENS DO SUPABASE
# ============================================================

def _obter_token_salvo() -> Optional[Dict[str, Any]]:
    """
    Obtém o registro OAuth salvo no Supabase.

    Nunca registra os tokens nos logs.
    """

    try:

        resposta = (
            _obter_supabase()
            .table(
                TOKEN_TABLE
            )
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
            .limit(
                1
            )
            .execute()
        )

    except Exception as erro:

        logger.exception(
            "Erro ao consultar tokens do Mercado Livre no Supabase."
        )

        raise MercadoLivreOAuthError(
            "Não foi possível consultar os tokens."
        ) from erro

    if not resposta.data:
        return None

    return resposta.data[0]


# ============================================================
# VERIFICAR VALIDADE DO ACCESS TOKEN
# ============================================================

def _token_ainda_valido(
    token_data: Dict[str, Any],
) -> bool:
    """
    Verifica se o access_token ainda possui uma margem
    segura antes da expiração.
    """

    expires_at = token_data.get(
        "expires_at"
    )

    if not expires_at:
        return False

    try:

        texto = str(
            expires_at
        ).replace(
            "Z",
            "+00:00",
        )

        data_expiracao = (
            datetime.fromisoformat(
                texto
            )
        )

        if data_expiracao.tzinfo is None:

            data_expiracao = (
                data_expiracao.replace(
                    tzinfo=timezone.utc
                )
            )

        agora = datetime.now(
            timezone.utc
        )

        segundos_restantes = (
            data_expiracao - agora
        ).total_seconds()

        return (
            segundos_restantes
            > TOKEN_REFRESH_MARGIN_SECONDS
        )

    except (
        TypeError,
        ValueError,
    ):

        return False


# ============================================================
# REFRESH TOKEN
# ============================================================

_refresh_lock = threading.Lock()


def renovar_access_token_mercadolivre(
    token_data: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Renova o access_token utilizando o refresh_token.

    IMPORTANTE:

    O Mercado Livre pode fornecer um novo refresh_token.
    O novo registro é salvo integralmente no Supabase,
    substituindo o refresh_token anterior.
    """

    validar_configuracao_oauth()

    if token_data is None:

        token_data = (
            _obter_token_salvo()
        )

    if not token_data:

        raise MercadoLivreOAuthError(
            "Nenhuma autorização do Mercado Livre foi encontrada."
        )

    refresh_token = token_data.get(
        "refresh_token"
    )

    if not refresh_token:

        raise MercadoLivreOAuthError(
            "Refresh token não encontrado."
        )

    dados = {
        "grant_type": "refresh_token",
        "client_id": MERCADOLIVRE_CLIENT_ID,
        "client_secret": MERCADOLIVRE_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    novo_token = _post_token(
        dados
    )

    # O novo access_token e o novo refresh_token
    # substituem os valores anteriores.
    salvar_tokens(
        novo_token
    )

    logger.info(
        "Access token do Mercado Livre renovado com sucesso."
    )

    return str(
        novo_token[
            "access_token"
        ]
    )


# ============================================================
# FUNÇÃO CENTRALIZADA
# ============================================================

def obter_access_token_mercadolivre() -> str:
    """
    Retorna sempre um access_token utilizável.

    Fluxo:

    1. Consulta Supabase.
    2. Verifica validade.
    3. Se estiver válido, retorna o token.
    4. Se estiver próximo da expiração, faz refresh.
    5. Salva access_token + refresh_token novos.
    6. Retorna o novo access_token.
    """

    token_data = (
        _obter_token_salvo()
    )

    # --------------------------------------------------------
    # Compatibilidade com instalações antigas
    # --------------------------------------------------------

    if not token_data:

        token_legado = os.getenv(
            "MERCADOLIVRE_ACCESS_TOKEN",
            "",
        ).strip()

        if token_legado:

            logger.warning(
                "Usando MERCADOLIVRE_ACCESS_TOKEN legado. "
                "Conclua o OAuth para migrar para o armazenamento "
                "persistente no Supabase."
            )

            return token_legado

        raise MercadoLivreOAuthError(
            "Mercado Livre não autorizado. "
            "Acesse /mercadolivre/login."
        )

    # --------------------------------------------------------
    # Token ainda válido
    # --------------------------------------------------------

    if _token_ainda_valido(
        token_data
    ):

        access_token = (
            token_data.get(
                "access_token"
            )
        )

        if access_token:

            return str(
                access_token
            )

    # --------------------------------------------------------
    # Token próximo da expiração
    # --------------------------------------------------------

    with _refresh_lock:

        # Outra thread pode ter feito refresh
        # enquanto esta aguardava o lock.
        token_atual = (
            _obter_token_salvo()
        )

        if (
            token_atual
            and _token_ainda_valido(
                token_atual
            )
        ):

            access_token = (
                token_atual.get(
                    "access_token"
                )
            )

            if access_token:

                return str(
                    access_token
                )

        return (
            renovar_access_token_mercadolivre(
                token_atual
            )
        )


# ============================================================
# PROCESSAR CALLBACK
# ============================================================

def processar_callback_mercadolivre(
    code: str,
    state: str,
) -> Dict[str, Any]:
    """
    Processa o callback OAuth.

    Primeiro valida o state.
    Somente depois troca o code por tokens.
    """

    if not code:

        raise MercadoLivreOAuthError(
            "Authorization code não recebido."
        )

    if not state:

        raise MercadoLivreOAuthError(
            "State OAuth não recebido."
        )

    # --------------------------------------------------------
    # IMPORTANTE:
    # Nunca aceitar state arbitrário.
    # --------------------------------------------------------

    if not validar_e_consumir_state(
        state
    ):

        raise MercadoLivreOAuthError(
            "State OAuth inválido ou expirado."
        )

    # --------------------------------------------------------
    # Trocar code por tokens
    # --------------------------------------------------------

    tokens = (
        trocar_code_por_tokens(
            code
        )
    )

    # --------------------------------------------------------
    # Persistir no Supabase
    # --------------------------------------------------------

    salvar_tokens(
        tokens
    )

    logger.info(
        "Autorização do Mercado Livre concluída. user_id=%s",
        tokens.get(
            "user_id"
        ),
    )

    return tokens


# ============================================================
# STATUS SEGURO
# ============================================================

def obter_status_mercadolivre() -> Dict[str, Any]:
    """
    Retorna somente informações seguras.

    Nunca retorna:
    - access_token
    - refresh_token
    - client_secret
    """

    token_data = (
        _obter_token_salvo()
    )

    if not token_data:

        return {
            "connected": False,
            "user_id": None,
            "expires_at": None,
            "token_valid": False,
        }

    return {
        "connected": bool(
            token_data.get(
                "access_token"
            )
            and token_data.get(
                "refresh_token"
            )
        ),

        "user_id": token_data.get(
            "user_id"
        ),

        "expires_at": token_data.get(
            "expires_at"
        ),

        "token_valid": (
            _token_ainda_valido(
                token_data
            )
        ),
    }
