import base64
import hashlib
import logging
import os
import time
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


logger = logging.getLogger("raposa-cacadora.manus_webhook")


def _normalizar_public_key(value: str) -> bytes:
    value = value.strip()
    if "\\n" in value:
        value = value.replace("\\n", "\n")
    return value.encode("utf-8")


def verificar_assinatura(
    body: bytes,
    signature: str,
    timestamp: str,
    request_url: str,
) -> bool:
    public_key = os.getenv("MANUS_WEBHOOK_PUBLIC_KEY", "").strip()
    if not public_key or not signature or not timestamp:
        return False

    try:
        ts = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - ts) > 300:
        return False

    parts = urlsplit(request_url)
    canonical_url = urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        "",
        "",
    ))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}.{canonical_url}.{body_hash}".encode("utf-8")

    try:
        key = serialization.load_pem_public_key(
            _normalizar_public_key(public_key)
        )
        key.verify(
            base64.b64decode(signature),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        logger.exception("Assinatura do webhook Manus inválida.")
        return False
