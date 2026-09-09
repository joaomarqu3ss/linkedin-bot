"""Subcomando `linkedin-bot doctor`: diagnóstico do ambiente.

Roda depois da instalação e sempre que algo parecer errado. Cada verificação diz
o que encontrou e, quando falha, como consertar.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import config_dir, credentials_path, settings
from .ui import ARROW, FAIL, OK, WARN, out

OK_, WARN_, FAIL_ = "ok", "warn", "fail"
_MARK = {OK_: OK, WARN_: WARN, FAIL_: FAIL}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, hint: str = "") -> None:
        self.checks.append(Check(name, status, detail, hint))

    @property
    def failed(self) -> bool:
        return any(c.status == FAIL_ for c in self.checks)


# --- verificações ---------------------------------------------------------


def check_system(r: Report) -> None:
    r.add(
        "Sistema",
        OK_,
        f"{platform.system()} {platform.release()} ({platform.machine()})",
    )


def check_python(r: Report) -> None:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} em {sys.executable}"
    if (v.major, v.minor) < (3, 12):
        r.add("Python", FAIL_, detail, "o bot exige Python 3.12 ou superior")
    else:
        r.add("Python", OK_, detail)


def check_install(r: Report) -> None:
    exe = shutil.which("linkedin-bot")
    if exe:
        r.add("Instalação", OK_, f"linkedin-bot {__version__} em {exe}")
        return

    hint = (
        "o executável não está no PATH. Se instalou com uv, adicione ao seu perfil:\n"
        + (
            '        $env:PATH += ";$env:USERPROFILE\\.local\\bin"'
            if sys.platform == "win32"
            else '        export PATH="$HOME/.local/bin:$PATH"'
        )
    )
    r.add("Instalação", WARN_, f"versão {__version__}, fora do PATH", hint)


def check_config_dir(r: Report) -> None:
    d = config_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d)
        os.close(fd)
        Path(tmp).unlink()
    except OSError as exc:
        r.add("Diretório de config", FAIL_, f"{d} não é gravável: {exc}",
              "verifique as permissões da sua pasta de usuário")
        return
    r.add("Diretório de config", OK_, f"{d} (gravável)")


def check_permissions(r: Report) -> None:
    path = credentials_path()
    if not path.exists():
        r.add("Permissões", OK_, "sem credenciais gravadas ainda")
        return

    if sys.platform == "win32":
        r.add("Permissões", OK_, "protegido pelas ACLs do perfil (Windows)")
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        r.add("Permissões", WARN_, f"{path} está {oct(mode)}",
              f"restrinja o acesso: chmod 600 {path}")
    else:
        r.add("Permissões", OK_, f"{oct(mode)} — só o seu usuário lê")


def check_port(r: Report) -> None:
    port = settings.callback_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            r.add("Porta do callback", WARN_, f"{port} está ocupada",
                  "feche o processo que a usa ou rode 'auth login --port OUTRA' "
                  "(e cadastre a redirect URI correspondente no app)")
            return
    r.add("Porta do callback", OK_, f"{port} livre")


def check_network(r: Report) -> None:
    import httpx

    for label, url in (
        ("www.linkedin.com", "https://www.linkedin.com/oauth/.well-known/openid-configuration"),
        ("api.linkedin.com", "https://api.linkedin.com/v2/userinfo"),
    ):
        try:
            resp = httpx.get(url, timeout=8.0)
        except httpx.HTTPError as exc:
            r.add(f"Rede: {label}", FAIL_, f"inacessível ({type(exc).__name__})",
                  "verifique conexão, proxy corporativo ou firewall")
            continue
        # 401 em /userinfo sem token é a resposta esperada: prova alcance.
        good = resp.status_code in (200, 401)
        r.add(f"Rede: {label}", OK_ if good else WARN_, f"HTTP {resp.status_code}")


def check_api_version(r: Report) -> None:
    r.add("Versão da API", OK_, f"LinkedIn-Version: {settings.api_version}",
          "" if settings.api_version >= "202601" else
          "versão antiga; defina LINKEDIN_API_VERSION se receber HTTP 426")


def check_auth(r: Report) -> None:
    from ..auth.store import TokenStore

    state = TokenStore().load()
    if state.app is None:
        r.add("App LinkedIn", WARN_, "não configurado",
              "rode 'linkedin-bot auth setup'")
        return
    r.add("App LinkedIn", OK_, f"client_id {state.app.client_id}")

    if state.token is None:
        r.add("Sessão", WARN_, "não autenticado", "rode 'linkedin-bot auth login'")
    elif state.token.dead:
        r.add("Sessão", FAIL_, "token revogado (HTTP 401)",
              "rode 'linkedin-bot auth login'")
    elif state.token.is_expired:
        r.add("Sessão", FAIL_, "token expirado", "rode 'linkedin-bot auth login'")
    else:
        d = state.token.days_remaining
        status = WARN_ if d <= 3 else OK_
        r.add("Sessão", status,
              f"{state.token.name} — expira em {d} dia(s)",
              "rode 'linkedin-bot auth login' para renovar" if status == WARN_ else "")


CHECKS = (
    check_system, check_python, check_install, check_config_dir,
    check_permissions, check_port, check_network, check_api_version, check_auth,
)


def cmd_doctor(args: argparse.Namespace) -> int:
    r = Report()
    out("\nlinkedin-bot doctor\n" + "─" * 60)
    for fn in CHECKS:
        try:
            fn(r)
        except Exception as exc:  # a verificação nunca deve derrubar o diagnóstico
            r.add(fn.__name__, FAIL_, f"erro inesperado: {exc}")

    width = max(len(c.name) for c in r.checks)
    for c in r.checks:
        out(f"  {_MARK[c.status]}  {c.name.ljust(width)}  {c.detail}")
        if c.hint:
            for line in c.hint.splitlines():
                out(f"     {ARROW} {line}" if not line.startswith("    ") else line)

    out("─" * 60)
    counts = {s: sum(1 for c in r.checks if c.status == s) for s in (OK_, WARN_, FAIL_)}
    out(f"  {counts[OK_]} ok  ·  {counts[WARN_]} aviso(s)  ·  {counts[FAIL_]} falha(s)\n")
    return 1 if r.failed else 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "doctor",
        help="verifica o ambiente e aponta o que corrigir",
        description="Diagnóstico do ambiente: sistema, Python, instalação, PATH, "
        "permissões, porta, rede e estado da autenticação.",
    )
    p.set_defaults(func=cmd_doctor)
