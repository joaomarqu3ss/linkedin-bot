"""Configuração e constantes da integração com a LinkedIn."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE = "https://api.linkedin.com/rest"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
ACCESS_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

DEVELOPER_APPS_URL = "https://www.linkedin.com/developers/apps"

# Escopos fixos. Alterar este conjunto invalida todos os tokens já emitidos.
SCOPES = ("openid", "profile", "w_member_social")

CALLBACK_PATH = "/callback"
CALLBACK_TIMEOUT_SECONDS = 300

# Dias restantes a partir dos quais `auth status` alerta sobre a expiração.
EXPIRY_WARNING_DAYS = 3

_VERSION_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])$")


class Settings(BaseSettings):
    """Configuração sobrescrevível por variáveis de ambiente `LINKEDIN_*`."""

    model_config = SettingsConfigDict(env_prefix="LINKEDIN_", extra="ignore")

    # A versão da API caduca (~1 ano). O override existe para destravar o bot
    # sem esperar um release quando a versão embutida for descontinuada.
    api_version: str = "202608"
    callback_port: int = 8765
    # O LinkedIn não documenta PKCE, mas processa `code_verifier` — enviar um
    # challenge que o servidor não registrou faz a troca do code falhar.
    # Desligado por padrão; LINKEDIN_PKCE=1 reativa para experimentação.
    pkce: bool = False

    @field_validator("api_version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if not _VERSION_RE.match(v):
            raise ValueError(f"LINKEDIN_API_VERSION deve estar no formato YYYYMM, recebido: {v!r}")
        return v

    @field_validator("callback_port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1024 <= v <= 65535:
            raise ValueError(f"LINKEDIN_CALLBACK_PORT deve estar entre 1024 e 65535, recebido: {v}")
        return v


settings = Settings()


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "linkedin-bot"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def redirect_uri(port: int) -> str:
    return f"http://localhost:{port}{CALLBACK_PATH}"
