"""Saída de terminal segura em qualquer plataforma.

Consoles Windows com codepage legada (cp1252) não conseguem representar ✓/→/⚠.
Detectamos a codificação real de stdout e caímos para ASCII quando necessário.
"""

from __future__ import annotations

import sys

_FANCY = {"ok": "✓", "fail": "✗", "warn": "⚠", "arrow": "→", "bullet": "•"}
_PLAIN = {"ok": "[ok]", "fail": "[X]", "warn": "[!]", "arrow": "->", "bullet": "-"}


def _supports_unicode() -> bool:
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(_FANCY.values()).encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


SYM = _FANCY if _supports_unicode() else _PLAIN

OK = SYM["ok"]
FAIL = SYM["fail"]
WARN = SYM["warn"]
ARROW = SYM["arrow"]
BULLET = SYM["bullet"]


def out(msg: str = "") -> None:
    print(msg, file=sys.stdout)


def err(msg: str = "") -> None:
    print(msg, file=sys.stderr)
