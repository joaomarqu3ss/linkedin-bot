"""Previews de uso único: o publish só aceita um post que passou antes pelo preview."""

from __future__ import annotations

import time
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

from ..posts.draft import Draft

UNKNOWN_PREVIEW = "preview desconhecido ou expirado; chame linkedin_preview_post de novo"


class PreviewStore:
    """Guarda `Draft`s por `draft.fingerprint`, com prazo. Uma instância por servidor."""

    def __init__(self, ttl: float = 900, now: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._now = now
        self._items: dict[str, tuple[Draft, float]] = {}

    def __len__(self) -> int:
        self._purge()
        return len(self._items)

    def put(self, draft: Draft) -> str:
        """Registra o draft e devolve o id. O mesmo conteúdo renova o prazo."""
        self._purge()
        preview_id = draft.fingerprint
        self._items[preview_id] = (draft, self._now() + self.ttl)
        return preview_id

    def get(self, preview_id: str) -> Draft | None:
        """Lê sem consumir; respeita o TTL."""
        self._purge()
        entry = self._items.get(preview_id)
        return entry[0] if entry else None

    def take(self, preview_id: str) -> Draft:
        """Remove o preview e o devolve; a segunda chamada com o mesmo id falha."""
        self._purge()
        entry = self._items.pop(preview_id, None)
        if entry is None:
            raise ToolError(UNKNOWN_PREVIEW)
        return entry[0]

    def _purge(self) -> None:
        now = self._now()
        for key in [k for k, (_, exp) in self._items.items() if exp <= now]:
            del self._items[key]
