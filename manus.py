import json
import logging
import os
from typing import Any
import requests
logger = logging.getLogger("raposa-cacadora.manus")
MANUS_API_URL = os.getenv("MANUS_API_URL", "https://api.manus.ai").rstrip("/")
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()
class ManusAPIError(RuntimeError): pass
def _headers() -> dict[str, str]:
    if not MANUS_API_KEY: raise ManusAPIError("MANUS_API_KEY não configurada.")
    return {"Content-Type":"application/json","x-manus-api-key":MANUS_API_KEY}
def enviar_mensagem_tarefa(task_id: str, content: str) -> dict[str, Any]:
    if not task_id: raise ManusAPIError("task_id ausente.")
    if not content.strip(): raise ManusAPIError("Mensagem Manus vazia.")
    response=requests.post(f"{MANUS_API_URL}/v2/task.sendMessage",headers=_headers(),json={"task_id":task_id,"message":{"content":content.strip()}},timeout=60)
    try: data=response.json()
    except ValueError as exc: raise ManusAPIError(f"Resposta inválida da Manus ao enviar mensagem (HTTP {response.status_code}).") from exc
    if response.status_code >= 400 or not data.get("ok",True):
        raise ManusAPIError(f"MANUS_HTTP_{response.status_code}: {data}")
    return data
def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    response=requests.get(f"{MANUS_API_URL}/v2/task.listMessages",headers=_headers(),params={"task_id":task_id,"order":"desc","limit":20},timeout=60)
    try: data=response.json()
    except ValueError as exc: raise ManusAPIError(f"Resposta inválida da Manus (HTTP {response.status_code}).") from exc
    if response.status_code >= 400 or not data.get("ok",True): raise ManusAPIError(f"MANUS_HTTP_{response.status_code}: {data}")
    return data
