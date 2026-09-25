"""Runtime guard for the lightweight Render web server.

The app uses ThreadingHTTPServer. A burst of Web App polling/image requests can
otherwise create too many simultaneous threads/sockets on a small instance.
Python imports sitecustomize automatically at startup, so this keeps the guard
out of the application logic itself.
"""
import socketserver
import threading

_MAX_HTTP_THREADS = 4
_http_slots = threading.BoundedSemaphore(_MAX_HTTP_THREADS)
_original_process_request_thread = socketserver.ThreadingMixIn.process_request_thread

def _guarded_process_request(self, request, client_address):
    _http_slots.acquire()
    try:
        _original_process_request_thread(self, request, client_address)
    finally:
        _http_slots.release()

def _limited_process_request(self, request, client_address):
    thread = threading.Thread(
        target=_guarded_process_request,
        args=(self, request, client_address),
        daemon=getattr(self, "daemon_threads", True),
    )
    thread.start()

socketserver.ThreadingMixIn.process_request = _limited_process_request
socketserver.ThreadingMixIn.daemon_threads = True
socketserver.ThreadingMixIn.block_on_close = False
