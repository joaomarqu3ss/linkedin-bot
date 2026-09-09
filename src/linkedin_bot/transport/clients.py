"""Clientes HTTP pré-configurados.

Duas portas, com contratos diferentes:

- `api_client`    → /rest/*, headers de versão obrigatórios, corpo JSON pequeno.
- `upload_client` → PUT de binário em uploadUrl assinada: outro host, sem headers
                    de versão, streaming e timeout longo.

Ambos compartilham o mesmo token e o mesmo tratamento de 401.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import httpx

from ..config import API_BASE, settings
from .errors import ApiVersionSunsetError, RateLimitError, TokenExpiredError
from .retry import RetryTransport

if TYPE_CHECKING:
    from ..auth.session import LinkedInAuth

API_TIMEOUT = httpx.Timeout(10.0, connect=10.0)
UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


class BearerAuth(httpx.Auth):
    """Injeta o Bearer e traduz 401 em `TokenExpiredError`.

    Ponto único: nenhum call site precisa lembrar de tratar token morto.
    """

    def __init__(self, session: "LinkedInAuth") -> None:
        self._session = session

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        request.headers["Authorization"] = f"Bearer {self._session.token}"
        response = yield request
        if response.status_code == 401:
            self._session.mark_dead()
            raise TokenExpiredError()


def _check_response(response: httpx.Response) -> None:
    if response.status_code == 426:
        raise ApiVersionSunsetError(settings.api_version)
    if response.status_code == 429:
        raise RateLimitError()


def api_client(session: "LinkedInAuth") -> httpx.Client:
    """Cliente para `https://api.linkedin.com/rest/*`."""
    return httpx.Client(
        base_url=API_BASE,
        headers={
            "LinkedIn-Version": settings.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        auth=BearerAuth(session),
        timeout=API_TIMEOUT,
        transport=RetryTransport(),
        event_hooks={"response": [_check_response]},
    )


def upload_client(
    session: "LinkedInAuth", *, send_authorization: bool = True
) -> httpx.Client:
    """Cliente para o PUT do binário na `uploadUrl` devolvida pela API.

    A `uploadUrl` já vem assinada. Os exemplos oficiais divergem quanto a enviar
    `Authorization` nessa perna; enviamos por padrão porque é o que o exemplo mais
    explícito (Documents API) faz, e é inócuo se ignorado. `send_authorization=False`
    desliga.
    """
    return httpx.Client(
        auth=BearerAuth(session) if send_authorization else None,
        timeout=UPLOAD_TIMEOUT,
        transport=RetryTransport(),
    )
