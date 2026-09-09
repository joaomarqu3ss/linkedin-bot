"""Persistência das credenciais em `~/.config/linkedin-bot/credentials.json`.

Guarda duas coisas com ciclos de vida distintos: as credenciais do app
(permanentes) e o token do membro (60 dias). Diretório 0700, arquivo 0600.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..config import credentials_path

DIR_MODE = 0o700
FILE_MODE = 0o600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class AppCredentials:
    """Credenciais do app LinkedIn registrado pelo usuário."""

    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Token:
    """Token de acesso do membro, com a identidade resolvida no login."""

    access_token: str
    obtained_at: datetime
    expires_at: datetime
    scopes: tuple[str, ...]
    person_urn: str
    name: str
    dead: bool = False

    @property
    def seconds_remaining(self) -> float:
        return (self.expires_at - _now()).total_seconds()

    @property
    def days_remaining(self) -> int:
        return int(self.seconds_remaining // 86400)

    @property
    def is_expired(self) -> bool:
        return self.dead or self.seconds_remaining <= 0


@dataclass(frozen=True)
class StoredState:
    app: AppCredentials | None = None
    token: Token | None = None


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or credentials_path()

    # --- leitura ---------------------------------------------------------

    def load(self) -> StoredState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return StoredState()

        app = None
        if raw.get("client_id") and raw.get("client_secret"):
            app = AppCredentials(raw["client_id"], raw["client_secret"])

        token = None
        if raw.get("access_token"):
            token = Token(
                access_token=raw["access_token"],
                obtained_at=_parse(raw["obtained_at"]),
                expires_at=_parse(raw["expires_at"]),
                scopes=tuple(raw.get("scopes", ())),
                person_urn=raw["person_urn"],
                name=raw.get("name", ""),
                dead=raw.get("dead", False),
            )

        return StoredState(app=app, token=token)

    # --- escrita ---------------------------------------------------------

    def save_app(self, app: AppCredentials) -> None:
        state = self.load()
        # Trocar de app invalida o token do app anterior.
        token = state.token if state.app == app else None
        self._write(StoredState(app=app, token=token))

    def save_token(self, token: Token) -> None:
        self._write(replace(self.load(), token=token))

    def mark_dead(self) -> None:
        state = self.load()
        if state.token and not state.token.dead:
            self._write(replace(state, token=replace(state.token, dead=True)))

    def clear_token(self) -> None:
        self._write(replace(self.load(), token=None))

    def clear_all(self) -> None:
        self.path.unlink(missing_ok=True)

    # --- interno ---------------------------------------------------------

    def _write(self, state: StoredState) -> None:
        payload: dict[str, object] = {}
        if state.app:
            payload["client_id"] = state.app.client_id
            payload["client_secret"] = state.app.client_secret
        if state.token:
            t = state.token
            payload.update(
                access_token=t.access_token,
                obtained_at=_iso(t.obtained_at),
                expires_at=_iso(t.expires_at),
                scopes=list(t.scopes),
                person_urn=t.person_urn,
                name=t.name,
                dead=t.dead,
            )

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        os.chmod(self.path.parent, DIR_MODE)

        # Escrita atômica: o arquivo nunca fica pela metade, e nunca existe um
        # instante em que o segredo esteja no disco com permissão frouxa.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".credentials-")
        try:
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
