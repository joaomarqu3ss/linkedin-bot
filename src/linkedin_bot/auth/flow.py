"""Fluxo OAuth 2.0 3-legged.

O LinkedIn não oferece device flow nem refresh token programático fora do
programa MDP: o token vale 60 dias e renovar exige um round-trip pelo browser.
Este módulo implementa exatamente esse round-trip.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import webbrowser
from datetime import timedelta
from typing import Callable
from urllib.parse import urlencode

import httpx

from ..config import (
    ACCESS_TOKEN_URL,
    AUTHORIZATION_URL,
    CALLBACK_TIMEOUT_SECONDS,
    SCOPES,
    settings,
    USERINFO_URL,
    redirect_uri,
)
from ..transport.errors import (
    AuthError,
    AuthorizationDeniedError,
    InvalidScopeError,
    NotConfiguredError,
    OAuthError,
    StateMismatchError,
)
from .callback import CallbackServer
from .store import AppCredentials, Token, TokenStore, _now

Notify = Callable[[str], None]


def generate_pkce() -> tuple[str, str]:
    """Devolve (code_verifier, code_challenge) no método S256.

    O LinkedIn não documenta suporte a PKCE, mas uma de suas mensagens de erro
    menciona `code verifier`. Enviamos de qualquer forma: se for ignorado, não
    custa nada; se for honrado, é uma defesa a mais.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_url(
    client_id: str, uri: str, state: str, code_challenge: str | None = None
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": uri,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


def exchange_code(
    app: AppCredentials, code: str, uri: str, code_verifier: str | None = None
) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": app.client_id,
        "client_secret": app.client_secret,
        "redirect_uri": uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    response = httpx.post(
        ACCESS_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise OAuthError(
            body.get("error", "unknown_error"),
            body.get("error_description", response.text[:200]),
            response.status_code,
        )
    return response.json()


def fetch_userinfo(access_token: str) -> dict:
    """Resolve a identidade do membro. O `sub` vira o `author` dos posts."""
    response = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise AuthError(
            "Token obtido, mas não foi possível ler o perfil em /v2/userinfo "
            f"(HTTP {response.status_code}). Confirme que os escopos 'openid' e "
            "'profile' estão habilitados no seu app."
        )
    return response.json()


def perform_login(
    store: TokenStore, port: int, notify: Notify = lambda _: None
) -> Token:
    """Executa o fluxo completo e persiste o token. Devolve o token gravado."""
    state = store.load()
    if state.app is None:
        raise NotConfiguredError()
    app = state.app

    verifier, challenge = generate_pkce() if settings.pkce else (None, None)
    oauth_state = secrets.token_urlsafe(32)
    uri = redirect_uri(port)

    # Reserva a porta antes de abrir o browser: falhar depois de mandar o usuário
    # para o LinkedIn seria desperdiçar a interação dele.
    server = CallbackServer(port)
    try:
        url = build_authorization_url(app.client_id, uri, oauth_state, challenge)
        notify("Abrindo o navegador para autorizar o acesso...")
        notify(f"Se nada abrir, acesse manualmente:\n{url}")
        webbrowser.open(url)

        result = server.wait(CALLBACK_TIMEOUT_SECONDS)
    except BaseException:
        server.close()
        raise

    if result.error:
        if "scope" in result.error.lower():
            raise InvalidScopeError(SCOPES, result.error_description or "")
        raise AuthorizationDeniedError(result.error, result.error_description or "")

    if not hmac.compare_digest(result.state or "", oauth_state):
        raise StateMismatchError()

    if not result.code:
        raise AuthError("O LinkedIn respondeu sem um 'code'. Refaça o login.")

    notify("Trocando o código por um token de acesso...")
    payload = exchange_code(app, result.code, uri, verifier)

    notify("Lendo seu perfil...")
    userinfo = fetch_userinfo(payload["access_token"])

    obtained = _now()
    scope = payload.get("scope") or " ".join(SCOPES)
    token = Token(
        access_token=payload["access_token"],
        obtained_at=obtained,
        expires_at=obtained + timedelta(seconds=int(payload["expires_in"])),
        scopes=tuple(scope.replace(",", " ").split()),
        person_urn=f"urn:li:person:{userinfo['sub']}",
        name=userinfo.get("name", ""),
    )
    store.save_token(token)
    return token
