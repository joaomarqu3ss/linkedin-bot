"""Instalação da skill para agentes de IA (`linkedin-bot --ai ALVO`)."""

from __future__ import annotations

from pathlib import Path

from ..agents.targets import TARGETS, display_path, install, resolve
from .ui import ARROW, OK, WARN, err, out


def _list() -> int:
    out("\nAlvos disponíveis para --ai\n" + "─" * 66)
    kw = max(len(t.key) for t in TARGETS)
    lw = max(len(t.label) for t in TARGETS)
    cwd = Path.cwd()
    for t in TARGETS:
        mark = OK if t.detected else " "
        out(f"  {mark} {t.key.ljust(kw)}  {t.label.ljust(lw)}  [{t.scope}]")
        out(f"    {' ' * kw}  {display_path(t.path(cwd))}")
    out("─" * 66)
    out(f"  {OK} = agente detectado nesta máquina\n")
    out("  linkedin-bot --ai claude    instala para um alvo")
    out("  linkedin-bot --ai all       instala para todos os detectados\n")
    return 0


def _install_one(key: str) -> int:
    target = resolve(key)
    if target is None:
        err(f"erro: alvo desconhecido '{key}'")
        err(f"  {ARROW} veja os alvos com: linkedin-bot --ai list")
        return 1

    dest, action = install(target)
    out(f"  {OK} {target.label}: skill {action}")
    out(f"    {display_path(dest)}")
    if target.scope == "projeto":
        out(f"    {ARROW} escopo de projeto: vale para {Path.cwd()}")
    return 0


def run(value: str) -> int:
    value = value.strip().lower()

    if value in ("list", "ls", "lista"):
        return _list()

    out("")
    if value == "all":
        detected = [t for t in TARGETS if t.detected]
        if not detected:
            err("erro: nenhum agente detectado nesta máquina")
            err(f"  {ARROW} instale para um alvo específico: linkedin-bot --ai claude")
            return 1
        rc = 0
        for t in detected:
            rc |= _install_one(t.key)
        out("")
        return rc

    rc = _install_one(value)
    if rc == 0:
        out(f"\n  {WARN} reinicie o agente para ele carregar a skill\n")
    return rc
