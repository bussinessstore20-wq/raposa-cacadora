import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from http.server import BaseHTTPRequestHandler

RENDER_URL = "https://raposa-cacadora.onrender.com"
HTML_PATH = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def proxy(path, method, body=b"", headers=None):
    target = f"{RENDER_URL}{path}"
    forward_headers = {}
    for key in ("Content-Type", "Accept", "X-Webhook-Signature", "X-Webhook-Timestamp", "X-Telegram-Init-Data"):
        if headers and headers.get(key):
            forward_headers[key] = headers[key]

    request = Request(
        target,
        data=body if method in ("POST", "PUT", "PATCH") else None,
        headers=forward_headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=25) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except URLError as error:
        payload = json.dumps({"error": f"Backend indisponível: {error.reason}"}).encode()
        return 502, {"Content-Type": "application/json; charset=utf-8"}, payload


class handler(BaseHTTPRequestHandler):
    def _send(self, status, headers, body):
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in {"content-length", "connection", "transfer-encoding"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-Webhook-Signature, X-Webhook-Timestamp, X-Telegram-Init-Data")
        self.end_headers()

    def do_GET(self):
        path = self.path

        if path == "/" or path == "":
            try:
                body = HTML_PATH.read_bytes()
            except OSError as error:
                body = json.dumps({"error": str(error)}).encode()
                self._send(500, {"Content-Type": "application/json; charset=utf-8"}, body)
                return

            self._send(200, {"Content-Type": "text/html; charset=utf-8"}, body)
            return

        if path == "/api/diagnostico":
            import time
            init_data = self.headers.get("X-Telegram-Init-Data", "")
            inicio = time.time()
            try:
                status, backend_headers, backend_body = proxy("/api/dashboard", "GET", headers=self.headers)
                try:
                    backend_json = json.loads(backend_body.decode("utf-8", errors="replace"))
                except Exception:
                    backend_json = None

                payload = {
                    "ok": True,
                    "proxy": {
                        "vercel_recebeu_init_data": bool(init_data),
                        "init_data_tamanho": len(init_data),
                        "backend_url": RENDER_URL,
                        "backend_alcancado": True,
                        "backend_status": status,
                        "tempo_ms": round((time.time() - inicio) * 1000),
                    },
                    "backend": backend_json if backend_json is not None else {
                        "raw_preview": backend_body.decode("utf-8", errors="replace")[:500]
                    },
                }
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, body)
            except Exception as error:
                payload = json.dumps({
                    "ok": False,
                    "proxy": {
                        "vercel_recebeu_init_data": bool(init_data),
                        "init_data_tamanho": len(init_data),
                        "backend_url": RENDER_URL,
                        "backend_alcancado": False,
                        "tempo_ms": round((time.time() - inicio) * 1000),
                    },
                    "erro_proxy": str(error),
                }, ensure_ascii=False).encode("utf-8")
                self._send(502, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, payload)
            return

        if path.startswith("/api/status") or path.startswith("/api/dashboard") or path == "/webhook/manus":
            status, headers, body = proxy(path, "GET", headers=self.headers)
            self._send(status, headers, body)
            return

        self._send(404, {"Content-Type": "application/json; charset=utf-8"}, b'{"error":"Not found"}')

    def do_POST(self):
        path = self.path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if path in ("/api/configurar", "/webhook/manus"):
            status, headers, response_body = proxy(path, "POST", body, self.headers)
            self._send(status, headers, response_body)
            return

        self._send(404, {"Content-Type": "application/json; charset=utf-8"}, b'{"error":"Not found"}')
