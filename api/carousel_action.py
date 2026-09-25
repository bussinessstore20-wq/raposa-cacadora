import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler

RENDER_URL = "https://raposa-cacadora.onrender.com"

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        headers = {}
        for key in ("Content-Type", "Accept", "X-Telegram-Init-Data"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        try:
            req = Request(RENDER_URL + "/api/carousel/action", data=body, headers=headers, method="POST")
            with urlopen(req, timeout=25) as response:
                status = response.status
                response_headers = dict(response.headers.items())
                response_body = response.read()
        except HTTPError as error:
            status = error.code
            response_headers = dict(error.headers.items())
            response_body = error.read()
        except URLError as error:
            status = 502
            response_headers = {"Content-Type": "application/json; charset=utf-8"}
            response_body = json.dumps({"ok": False, "error": "backend_unavailable", "message": "Backend indisponivel"}).encode()
        self.send_response(status)
        for key, value in response_headers.items():
            if key.lower() in {"content-length", "connection", "transfer-encoding"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-Telegram-Init-Data")
        self.end_headers()
