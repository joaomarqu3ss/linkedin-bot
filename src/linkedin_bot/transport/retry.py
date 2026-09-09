"""Retry com backoff exponencial.

Aplicado no nível de transporte para valer igualmente no cliente de API e no de
upload. Requisições cujo corpo é um stream (upload de arquivo) não são
retentadas: o corpo já foi consumido e não é reproduzível.
"""

from __future__ import annotations

import random
import time

import httpx

RETRY_STATUSES = frozenset({409, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 3
BASE_DELAY = 0.5
MAX_DELAY = 8.0


def _is_replayable(request: httpx.Request) -> bool:
    try:
        request.content
    except Exception:
        return False
    return True


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), MAX_DELAY)
        except ValueError:
            pass
    delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
    return delay + random.uniform(0, delay * 0.1)


class RetryTransport(httpx.BaseTransport):
    def __init__(
        self,
        wrapped: httpx.BaseTransport | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._wrapped = wrapped or httpx.HTTPTransport()
        self._max_attempts = max_attempts

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        replayable = _is_replayable(request)

        for attempt in range(1, self._max_attempts + 1):
            last = attempt == self._max_attempts

            try:
                response = self._wrapped.handle_request(request)
            except httpx.TransportError:
                if last or not replayable:
                    raise
                time.sleep(_backoff(attempt, None))
                continue

            if last or not replayable or response.status_code not in RETRY_STATUSES:
                return response

            retry_after = response.headers.get("retry-after")
            response.read()
            response.close()
            time.sleep(_backoff(attempt, retry_after))

        raise AssertionError("inalcançável")  # pragma: no cover

    def close(self) -> None:
        self._wrapped.close()
