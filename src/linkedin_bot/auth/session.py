"""A sessão autenticada consumida pelo resto do bot.

Expõe `person_urn` (a camada de posts precisa dele para o campo `author`) e
`expires_at`, mas mantém o token encapsulado: quem quer falar com a API pede um
cliente, não uma string.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from ..transport.errors import NotAuthenticatedError, NotConfiguredError, TokenExpiredError
from .store import Token, TokenStore


class LinkedInAuth:
    def __init__(self, store: TokenStore, token: Token) -> None:
        self._store = store
        self._token = token

    @classmethod
    def load(cls, store: TokenStore | None = None) -> "LinkedInAuth":
        store = store or TokenStore()
        state = store.load()
        if state.app is None:
            raise NotConfiguredError()
        if state.token is None:
            raise NotAuthenticatedError()
        if state.token.is_expired:
            raise TokenExpiredError()
        return cls(store, state.token)

    # --- identidade ------------------------------------------------------

    @property
    def token(self) -> str:
        return self._token.access_token

    @property
    def person_urn(self) -> str:
        return self._token.person_urn

    @property
    def name(self) -> str:
        return self._token.name

    @property
    def expires_at(self) -> datetime:
        return self._token.expires_at

    @property
    def days_remaining(self) -> int:
        return self._token.days_remaining

    def mark_dead(self) -> None:
        """Marca o token como inutilizável. Chamado pelo hook ao receber 401."""
        self._store.mark_dead()
        self._token = type(self._token)(
            **{**self._token.__dict__, "dead": True}
        )

    # --- transporte ------------------------------------------------------

    def api_client(self) -> httpx.Client:
        from ..transport.clients import api_client

        return api_client(self)

    def upload_client(self, *, send_authorization: bool = True) -> httpx.Client:
        from ..transport.clients import upload_client

        return upload_client(self, send_authorization=send_authorization)
