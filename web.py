import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from supabase import create_client

from instagram_pipeline import processar_webhook_manus
from webhook import verificar_assinatura


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | raposa-cacadora-web | %(message)s",
)
logger = logging.getLogger("raposa-cacadora-web")

PORT = int(os.getenv("PORT", "10000"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL e SUPABASE_KEY são obrigatórias.")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "service": "raposa-cacadora-web"})
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path.split("?", 1)[0].rstrip("/") != "/webhook/manus":
            self._send(404, {"ok": False, "error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        signature = self.headers.get("X-Webhook-Signature", "")
        timestamp = self.headers.get("X-Webhook-Timestamp", "")

        if not verificar_assinatura(
            body,
            signature,
            timestamp,
            f"https://{self.headers.get('Host', '')}/webhook/manus",
        ):
            self._send(401, {"ok": False, "error": "invalid_signature"})
            return

        try:
            payload = json.loads(body.decode("utf-8"))
            ok, message = processar_webhook_manus(supabase, payload)
            self._send(200 if ok else 500, {"ok": ok, "message": message})
        except Exception as exc:
            logger.exception("Erro no webhook Manus.")
            self._send(500, {"ok": False, "error": str(exc)[:1000]})

    def log_message(self, fmt, *args):
        return


def main():
    logger.info("Webhook server iniciado em 0.0.0.0:%d", PORT)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
