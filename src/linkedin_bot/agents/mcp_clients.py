"""Registro do servidor MCP `linkedin-bot` em cada cliente de IA.

Em todos os clientes o servidor se chama `linkedin-bot` e o comando registrado é o
caminho absoluto do executável, com `args = ["mcp"]`: clientes GUI (Cursor,
Antigravity) não herdam o PATH do shell, e `~/.local/bin` costuma ficar de fora.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import targets
from .targets import _merge_section

NAME = "linkedin-bot"
TOML_START = "# linkedin-bot:start"
TOML_END = "# linkedin-bot:end"
CLI_TIMEOUT = 60

OK = "ok"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class Result:
    status: str                 # OK, SKIPPED ou FAILED
    detail: str = ""            # escopo/motivo, em uma linha
    path: Path | None = None    # arquivo gravado, quando houve
    manual: str = ""            # comando ou trecho para o usuário aplicar à mão


@dataclass(frozen=True)
class Client:
    key: str
    dest: Callable[[], str]
    detect: Callable[[], bool]
    register: Callable[[str], Result]


def exe_path() -> str:
    """Caminho absoluto do executável: PATH primeiro, depois o argv[0] resolvido."""
    found = shutil.which(NAME)
    if found:
        return os.path.abspath(found)
    return str(Path(sys.argv[0]).resolve())


def _home() -> Path:
    return targets._home()


def _on_path(binary: str) -> bool:
    return shutil.which(binary) is not None


# --- escrita ------------------------------------------------------------------


def _read(path: Path) -> str:
    # bytes, para que o conteúdo ao redor do bloco volte idêntico (inclusive CRLF)
    return path.read_bytes().decode("utf-8")


def _atomic_write(path: Path, text: str) -> None:
    """Temporário no mesmo diretório e os.replace; preserva o modo do arquivo original."""
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- claude e hermes: via CLI ---------------------------------------------------


def _claude_cmd(exe: str) -> str:
    return f"claude mcp add -s user {NAME} -- {exe} mcp"


def _hermes_cmd(exe: str) -> str:
    return f"hermes mcp add {NAME} --command {exe} --args mcp"


def _run(argv: list[str], *, capture: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        stdin=subprocess.DEVNULL if capture else None,
        timeout=CLI_TIMEOUT if capture else None,  # sem timeout quando o usuário responde
    )


def register_claude(exe: str) -> Result:
    binary = shutil.which("claude")
    manual = _claude_cmd(exe)
    if binary is None:
        return Result(SKIPPED, "binário `claude` não encontrado", manual=manual)
    try:
        _run([binary, "mcp", "remove", "-s", "user", NAME], capture=True)  # falha ignorada
        added = _run([binary, "mcp", "add", "-s", "user", NAME, "--", exe, "mcp"], capture=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(FAILED, str(exc), manual=manual)
    if added.returncode != 0:
        reason = (added.stderr or added.stdout or "").strip() or f"exit {added.returncode}"
        return Result(FAILED, reason, manual=manual)
    return Result(OK, "escopo user")


def register_hermes(exe: str) -> Result:
    binary = shutil.which("hermes")
    manual = _hermes_cmd(exe)
    if binary is None:
        return Result(SKIPPED, "binário `hermes` não encontrado", manual=manual)
    if sys.stdin is None or not sys.stdin.isatty():
        # `hermes mcp add` descobre as tools antes de gravar e pode fazer perguntas
        return Result(SKIPPED, "terminal não interativo", manual=manual)
    try:
        _run([binary, "mcp", "remove", NAME], capture=True)  # falha ignorada
        added = _run([binary, "mcp", "add", NAME, "--command", exe, "--args", "mcp"], capture=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(FAILED, str(exc), manual=manual)
    if added.returncode != 0:
        return Result(FAILED, f"exit {added.returncode}", manual=manual)
    return Result(OK)


# --- codex: bloco TOML ----------------------------------------------------------


def codex_block(exe: str) -> str:
    """Corpo do bloco, sem marcadores. json.dumps gera strings básicas TOML válidas."""
    return (
        f"[mcp_servers.{NAME}]\n"
        f"command = {json.dumps(exe)}\n"
        'args = ["mcp"]\n'
        "tool_timeout_sec = 300\n"
        "\n"
        f"[mcp_servers.{NAME}.tools.linkedin_publish_post]\n"
        'approval_mode = "prompt"\n'
    )


def _codex_manual(exe: str) -> str:
    return f"{TOML_START}\n{codex_block(exe)}{TOML_END}"


def _strip_block(text: str) -> str:
    start, end = text.find(TOML_START), text.find(TOML_END)
    if start == -1 or end < start:
        return text
    return text[:start] + text[end + len(TOML_END):]


def register_codex(exe: str) -> Result:
    path = _home() / ".codex" / "config.toml"
    manual = _codex_manual(exe)
    try:
        existing = _read(path) if path.exists() else ""
        servers = tomllib.loads(_strip_block(existing)).get("mcp_servers", {})
        if isinstance(servers, dict) and NAME in servers:
            return Result(
                SKIPPED, f"{NAME} já definido fora dos marcadores", path=path, manual=manual
            )
        merged = _merge_section(existing, codex_block(exe), TOML_START, TOML_END)
        tomllib.loads(merged)
        if merged != existing:
            _atomic_write(path, merged)
    except tomllib.TOMLDecodeError:
        return Result(
            SKIPPED, "config.toml não é TOML válido ou conflita com o bloco", path=path, manual=manual
        )
    except (OSError, UnicodeDecodeError) as exc:
        return Result(FAILED, str(exc), path=path, manual=manual)
    return Result(OK, path=path)


# --- gemini, antigravity, cursor: merge JSON ---------------------------------------


def _json_entry(exe: str, *, stdio: bool) -> dict:
    entry = {"command": exe, "args": ["mcp"]}
    return {"type": "stdio", **entry} if stdio else entry


def _json_manual(exe: str, *, stdio: bool) -> str:
    snippet = {"mcpServers": {NAME: _json_entry(exe, stdio=stdio)}}
    return json.dumps(snippet, indent=2, ensure_ascii=False)


def _merge_json(path: Path, exe: str, *, stdio: bool) -> Result:
    manual = _json_manual(exe, stdio=stdio)
    try:
        raw = _read(path) if path.exists() else ""
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return Result(SKIPPED, "o arquivo não é JSON válido", path=path, manual=manual)
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            return Result(SKIPPED, "formato inesperado no arquivo", path=path, manual=manual)
        data.setdefault("mcpServers", {})[NAME] = _json_entry(exe, stdio=stdio)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if text != raw:
            _atomic_write(path, text)
    except (OSError, UnicodeDecodeError) as exc:
        return Result(FAILED, str(exc), path=path, manual=manual)
    return Result(OK, path=path)


def _gemini_file() -> Path:
    return _home() / ".gemini" / "settings.json"


def _antigravity_file() -> Path:
    return _home() / ".gemini" / "config" / "mcp_config.json"


def _cursor_file() -> Path:
    return _home() / ".cursor" / "mcp.json"


CLIENTS: dict[str, Client] = {
    "claude": Client(
        key="claude",
        dest=lambda: "claude mcp add (escopo user)",
        detect=lambda: _on_path("claude"),
        register=register_claude,
    ),
    "codex": Client(
        key="codex",
        dest=lambda: targets.display_path(_home() / ".codex" / "config.toml"),
        detect=lambda: (_home() / ".codex").is_dir(),
        register=register_codex,
    ),
    "gemini": Client(
        key="gemini",
        dest=lambda: targets.display_path(_gemini_file()),
        detect=lambda: _on_path("gemini") or _gemini_file().exists(),
        register=lambda exe: _merge_json(_gemini_file(), exe, stdio=False),
    ),
    "hermes": Client(
        key="hermes",
        dest=lambda: "hermes mcp add (~/.hermes/config.yaml)",
        detect=lambda: _on_path("hermes"),
        register=register_hermes,
    ),
    "antigravity": Client(
        key="antigravity",
        dest=lambda: targets.display_path(_antigravity_file()),
        detect=lambda: _antigravity_file().parent.is_dir() or _on_path("agy"),
        register=lambda exe: _merge_json(_antigravity_file(), exe, stdio=False),
    ),
    "cursor": Client(
        key="cursor",
        dest=lambda: targets.display_path(_cursor_file()),
        detect=lambda: targets.BY_KEY["cursor"].detected,
        register=lambda exe: _merge_json(_cursor_file(), exe, stdio=True),
    ),
}


def client_for(key: str) -> Client | None:
    return CLIENTS.get(key)
