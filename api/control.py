import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RENDER_URL = "https://raposa-cacadora.onrender.com"


def handler(request):
    try:
        if request.method == "OPTIONS":
            return {"statusCode": 204, "headers": {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data"}, "body": ""}
        if request.method != "POST":
            return {"statusCode": 405, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"ok": False, "error": "method_not_allowed"})}
        raw = request.body or "{}"
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        body = raw.encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        init_data = request.headers.get("x-telegram-init-data") or request.headers.get("X-Telegram-Init-Data") or ""
        if init_data:
            headers["X-Telegram-Init-Data"] = init_data
        upstream = Request(f"{RENDER_URL}/api/control", data=body, headers=headers, method="POST")
        with urlopen(upstream, timeout=25) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return {"statusCode": response.status, "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, "body": payload}
    except HTTPError as error:
        payload = error.read().decode("utf-8", errors="replace")
        return {"statusCode": error.code, "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, "body": payload or json.dumps({"ok": False, "error": "backend_http_error"})}
    except URLError as error:
        return {"statusCode": 502, "headers": {"Content-Type": "application/json; charset=utf-8"}, "body": json.dumps({"ok": False, "error": "backend_unavailable", "message": str(error.reason)})}
    except Exception as error:
        return {"statusCode": 500, "headers": {"Content-Type": "application/json; charset=utf-8"}, "body": json.dumps({"ok": False, "error": "proxy_internal_error", "message": str(error)})}
