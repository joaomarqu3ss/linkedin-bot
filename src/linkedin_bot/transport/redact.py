"""Redação de segredos.

Tokens, client secrets e authorization codes nunca devem chegar a stdout, stderr
ou a um arquivo de log — nem em modo verboso.
"""

from __future__ import annotations

import logging
import re

MASK = "***REDACTED***"

_PATTERNS = (
    # Bearer <token>
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    # pares chave=valor em corpos x-www-form-urlencoded e query strings
    re.compile(
        r"((?:client_secret|code|access_token|refresh_token|code_verifier)=)[^&\s\"']+",
        re.IGNORECASE,
    ),
    # pares chave: valor em JSON
    re.compile(
        r"(\"(?:client_secret|code|access_token|refresh_token|code_verifier)\"\s*:\s*\")[^\"]+",
        re.IGNORECASE,
    ),
    # a query assinada das URLs de upload (ut=...)
    re.compile(r"([?&]ut=)[^&\s\"']+"),
)


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """Aplica `redact` à mensagem e aos argumentos de cada registro de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def install(logger: logging.Logger | None = None) -> None:
    (logger or logging.getLogger()).addFilter(RedactingFilter())
