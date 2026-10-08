"""Estado da sessão como dado puro, sem saída em terminal.

Compartilhado por `linkedin-bot doctor` e pela tool MCP `linkedin_status`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..config import EXPIRY_WARNING_DAYS
from .store import TokenStore

_LOGIN = "linkedin-bot auth login"


@dataclass(frozen=True)
class SessionStatus:
    configured: bool
    client_id: str | None = None
    authenticated: bool = False
    name: str | None = None
    person_urn: str | None = None
    expires_at: datetime | None = None
    days_remaining: int | None = None
    problem: str | None = None
    fix: str | None = None


def session_status(store: TokenStore | None = None) -> SessionStatus:
    state = (store or TokenStore()).load()

    if state.app is None:
        return SessionStatus(
            configured=False,
            problem="nenhum app LinkedIn configurado",
            fix="linkedin-bot auth setup",
        )

    app = {"configured": True, "client_id": state.app.client_id}
    token = state.token
    if token is None:
        return SessionStatus(**app, problem="não autenticado", fix=_LOGIN)

    days = token.days_remaining
    identity = {
        "name": token.name,
        "person_urn": token.person_urn,
        "expires_at": token.expires_at,
        "days_remaining": days,
    }
    if token.dead:
        return SessionStatus(**app, **identity, problem="token revogado (HTTP 401)", fix=_LOGIN)
    if token.is_expired:
        return SessionStatus(**app, **identity, problem="token expirado", fix=_LOGIN)

    if days <= EXPIRY_WARNING_DAYS:
        return SessionStatus(
            **app, **identity, authenticated=True,
            problem=f"token expira em {days} dia(s)", fix=_LOGIN,
        )
    return SessionStatus(**app, **identity, authenticated=True)
