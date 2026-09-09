"""Servidor HTTP efêmero que captura o redirect do LinkedIn.

Escuta em 127.0.0.1 numa porta fixa (a redirect URI é comparada como string exata
pelo LinkedIn, então porta efêmera não serve). Atende uma única requisição útil e
encerra.
"""

from __future__ import annotations

import errno
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ..config import CALLBACK_PATH
from ..transport.errors import CallbackTimeoutError, PortInUseError

# Fecha a aba se o browser permitir; caso contrário, deixa uma linha discreta.
# O terminal é a fonte de verdade do sucesso.
_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>linkedin-bot</title>"
    b"<script>window.close()</script>"
    b"<p style=\"font:14px system-ui;padding:2rem\">Pode fechar esta aba.</p>"
)


@dataclass(frozen=True)
class CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class CallbackServer:
    """Uso: instanciar (reserva a porta), abrir o browser, chamar `wait`."""

    def __init__(self, port: int, path: str = CALLBACK_PATH) -> None:
        self.port = port
        self._path = path
        self._result: CallbackResult | None = None

        handler = self._build_handler()
        try:
            self._server = HTTPServer(("127.0.0.1", port), handler)
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                raise PortInUseError(port) from exc
            raise

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != outer._path:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                params = parse_qs(parsed.query)
                outer._result = CallbackResult(
                    code=params.get("code", [None])[0],
                    state=params.get("state", [None])[0],
                    error=params.get("error", [None])[0],
                    error_description=params.get("error_description", [None])[0],
                )

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_PAGE)))
                self.end_headers()
                self.wfile.write(_PAGE)

            def log_message(self, *args: object) -> None:
                """Silencia o log padrão do http.server em stderr."""

        return Handler

    def wait(self, timeout: int) -> CallbackResult:
        deadline = time.monotonic() + timeout
        try:
            while self._result is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CallbackTimeoutError(timeout)
                self._server.timeout = remaining
                self._server.handle_request()
            return self._result
        finally:
            self.close()

    def close(self) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None
