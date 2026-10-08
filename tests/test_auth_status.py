"""`session_status`: os seis estados da tabela, e o `check_auth` que a consome."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linkedin_bot.auth.status import SessionStatus, session_status
from linkedin_bot.auth.store import AppCredentials, Token, TokenStore
from linkedin_bot.cli.doctor_cmd import Report, check_auth

LOGIN = "linkedin-bot auth login"


def _token(days: float, *, dead: bool = False) -> Token:
    now = datetime.now(timezone.utc)
    return Token(
        access_token="tok",
        obtained_at=now,
        expires_at=now + timedelta(days=days, hours=1),
        scopes=("w_member_social",),
        person_urn="urn:li:person:abc",
        name="Ana",
        dead=dead,
    )


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return TokenStore(tmp_path / "linkedin-bot" / "credentials.json")


@pytest.fixture
def with_app(store):
    store.save_app(AppCredentials("cid", "secret"))
    return store


def test_sem_app(store):
    s = session_status(store)
    assert not s.configured and not s.authenticated
    assert s.client_id is None and s.expires_at is None
    assert (s.problem, s.fix) == ("nenhum app LinkedIn configurado", "linkedin-bot auth setup")


def test_sem_token(with_app):
    s = session_status(with_app)
    assert s.configured and s.client_id == "cid" and not s.authenticated
    assert (s.problem, s.fix) == ("não autenticado", LOGIN)


def test_token_revogado(with_app):
    with_app.save_token(_token(30, dead=True))
    s = session_status(with_app)
    assert not s.authenticated
    assert (s.problem, s.fix) == ("token revogado (HTTP 401)", LOGIN)


def test_token_expirado(with_app):
    with_app.save_token(_token(-2))
    s = session_status(with_app)
    assert not s.authenticated
    assert (s.problem, s.fix) == ("token expirado", LOGIN)


def test_token_perto_de_expirar(with_app):
    with_app.save_token(_token(2))
    s = session_status(with_app)
    assert s.authenticated and s.days_remaining == 2
    assert (s.problem, s.fix) == ("token expira em 2 dia(s)", LOGIN)


def test_valido(with_app):
    with_app.save_token(_token(30))
    s = session_status(with_app)
    assert s.authenticated and s.days_remaining == 30
    assert (s.name, s.person_urn) == ("Ana", "urn:li:person:abc")
    assert s.expires_at is not None
    assert (s.problem, s.fix) == (None, None)


def test_status_e_imutavel(store):
    with pytest.raises(Exception):
        session_status(store).problem = "x"  # type: ignore[misc]
    assert isinstance(session_status(store), SessionStatus)


# --- regressão: linhas que check_auth adiciona ao Report ------------------


def _lines() -> list[tuple[str, str, str, str]]:
    r = Report()
    check_auth(r)  # lê o mesmo arquivo via XDG_CONFIG_HOME
    return [(c.name, c.status, c.detail, c.hint) for c in r.checks]


def test_check_auth_sem_app(store):
    assert _lines() == [
        ("App LinkedIn", "warn", "não configurado", "rode 'linkedin-bot auth setup'"),
    ]


def test_check_auth_sem_token(with_app):
    assert _lines() == [
        ("App LinkedIn", "ok", "client_id cid", ""),
        ("Sessão", "warn", "não autenticado", "rode 'linkedin-bot auth login'"),
    ]


def test_check_auth_valido(with_app):
    with_app.save_token(_token(30))
    assert _lines()[1] == ("Sessão", "ok", "Ana — expira em 30 dia(s)", "")


def test_check_auth_perto_de_expirar(with_app):
    with_app.save_token(_token(2))
    assert _lines()[1] == (
        "Sessão", "warn", "Ana — expira em 2 dia(s)",
        "rode 'linkedin-bot auth login' para renovar",
    )


def test_check_auth_expirado_e_revogado(with_app):
    with_app.save_token(_token(-2))
    assert _lines()[1] == (
        "Sessão", "fail", "token expirado", "rode 'linkedin-bot auth login'")
    with_app.save_token(_token(30, dead=True))
    assert _lines()[1] == (
        "Sessão", "fail", "token revogado (HTTP 401)", "rode 'linkedin-bot auth login'")
