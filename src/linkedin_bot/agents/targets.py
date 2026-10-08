"""Alvos de instalação da skill, um por agente de IA.

Cada alvo sabe onde o agente lê instruções e em que formato. Os caminhos vêm da
documentação de cada ferramenta, não de convenção inventada.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import content

START = "<!-- linkedin-bot:start -->"
END = "<!-- linkedin-bot:end -->"

FILE = "file"        # o arquivo inteiro é nosso
SECTION = "section"  # convivemos com outro conteúdo, entre marcadores


def _home() -> Path:
    return Path.home()


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    scope: str          # "global" ou "projeto"
    mode: str           # FILE ou SECTION
    render: Callable[[], str]
    _path: Callable[[Path], Path]
    _detect: Callable[[], bool]

    def path(self, cwd: Path) -> Path:
        return self._path(cwd)

    @property
    def detected(self) -> bool:
        try:
            return self._detect()
        except OSError:
            return False


TARGETS: tuple[Target, ...] = (
    Target(
        key="claude",
        label="Claude Code",
        scope="global",
        mode=FILE,
        render=content.skill_md,
        _path=lambda cwd: _home() / ".claude" / "skills" / "linkedin-bot" / "SKILL.md",
        _detect=lambda: (_home() / ".claude").is_dir(),
    ),
    Target(
        key="codex",
        label="OpenAI Codex",
        scope="global",
        mode=SECTION,
        render=content.plain,
        _path=lambda cwd: _home() / ".codex" / "AGENTS.md",
        _detect=lambda: (_home() / ".codex").is_dir(),
    ),
    Target(
        key="gemini",
        label="Gemini CLI / Antigravity (global)",
        scope="global",
        mode=SECTION,
        render=content.plain,
        _path=lambda cwd: _home() / ".gemini" / "GEMINI.md",
        _detect=lambda: (_home() / ".gemini").is_dir(),
    ),
    Target(
        key="hermes",
        label="Hermes",
        scope="global",
        mode=FILE,
        render=content.skill_md,
        _path=lambda cwd: _home() / ".hermes" / "skills" / "linkedin-bot" / "SKILL.md",
        _detect=lambda: (_home() / ".hermes").is_dir(),
    ),
    Target(
        key="antigravity",
        label="Google Antigravity (workspace)",
        scope="projeto",
        mode=FILE,
        render=content.plain,
        _path=lambda cwd: cwd / ".agents" / "rules" / "linkedin-bot.md",
        _detect=lambda: (Path.cwd() / ".agents").is_dir()
        or (Path.cwd() / ".agent").is_dir(),
    ),
    Target(
        key="cursor",
        label="Cursor",
        scope="projeto",
        mode=FILE,
        render=content.mdc,
        _path=lambda cwd: cwd / ".cursor" / "rules" / "linkedin-bot.mdc",
        _detect=lambda: (Path.cwd() / ".cursor").is_dir()
        or (_home() / ".cursor").is_dir(),
    ),
    Target(
        key="agents",
        label="AGENTS.md (padrão entre ferramentas)",
        scope="projeto",
        mode=SECTION,
        render=content.plain,
        _path=lambda cwd: cwd / "AGENTS.md",
        _detect=lambda: True,
    ),
)

BY_KEY = {t.key: t for t in TARGETS}


def resolve(name: str) -> Target | None:
    return BY_KEY.get(name.strip().lower())


def _merge_section(existing: str, block: str, start: str = START, end: str = END) -> str:
    """Substitui a seção entre marcadores, ou acrescenta ao fim.

    Idempotente: reinstalar atualiza no lugar em vez de duplicar.
    """
    wrapped = f"{start}\n{block.rstrip()}\n{end}\n"
    if start in existing and end in existing:
        # função, não string: o bloco pode ter barras invertidas (caminhos do Windows)
        return re.sub(
            re.escape(start) + r".*?" + re.escape(end) + r"\n?",
            lambda _match: wrapped,
            existing,
            flags=re.DOTALL,
        )
    if not existing:
        return wrapped
    separator = "" if existing.endswith("\n\n") else "\n"
    return f"{existing}{separator}\n{wrapped}"


def install(target: Target, cwd: Path | None = None) -> tuple[Path, str]:
    """Grava a skill. Devolve (caminho, ação) — ação é 'criado' ou 'atualizado'."""
    dest = target.path(cwd or Path.cwd())
    dest.parent.mkdir(parents=True, exist_ok=True)

    body = target.render()
    existed = dest.exists()

    if target.mode == FILE:
        dest.write_text(body, encoding="utf-8")
    else:
        current = dest.read_text(encoding="utf-8") if existed else ""
        dest.write_text(_merge_section(current, body), encoding="utf-8")

    return dest, "atualizado" if existed else "criado"


def is_current(target: Target, cwd: Path | None = None) -> bool | None:
    """A skill instalada bate com a desta versão? None quando não está instalada."""
    dest = target.path(cwd or Path.cwd())
    try:
        text = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if target.mode == FILE:
        return text == target.render()
    match = re.search(re.escape(START) + r"\n?(.*?)" + re.escape(END), text, flags=re.DOTALL)
    if match is None:
        return None
    return match.group(1).strip() == target.render().strip()


def display_path(p: Path) -> str:
    """Encurta o home para ~ (ou %USERPROFILE% no Windows)."""
    try:
        rel = p.relative_to(_home())
    except ValueError:
        return str(p)
    prefix = "%USERPROFILE%" if sys.platform == "win32" else "~"
    return os.path.join(prefix, *rel.parts)
