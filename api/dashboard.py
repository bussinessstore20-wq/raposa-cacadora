import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler

RENDER_URL = "https://raposa-cacadora.onrender.com"

def proxy(method, body=b"", headers=None):
    target = RENDER_URL + ("/api/carousel/action" if method == "POST" else "/api/dashboard")
    forwarded = {}
    for key in ("Content-Type", "Accept", "X-Telegram-Init-Data"):
        if headers and headers.get(key):
            forwarded[key] = headers[key]
    req = Request(target, data=body if method == "POST" else None, headers=forwarded, method=method)
    try:
        with urlopen(req, timeout=25) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except URLError as error:
        return 502, {"Content-Type": "application/json; charset=utf-8"}, json.dumps({"ok": False, "error": str(error.reason)}).encode()

class handler(BaseHTTPRequestHandler):
    def send_result(self, status, headers, body):
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() not in {"content-length", "connection", "transfer-encoding"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        status, headers, body = proxy("GET", headers=self.headers)
        self.send_result(status, headers, body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if payload.get("action") not in ("approve", "reject", "reenviar", "retry") or payload.get("post_id") is None:
            self.send_result(400, {"Content-Type":"application/json; charset=utf-8"}, json.dumps({"ok":False,"error":"acao_invalida"}).encode())
            return
        status, headers, response_body = proxy("POST", body, self.headers)
        self.send_result(status, headers, response_body)
