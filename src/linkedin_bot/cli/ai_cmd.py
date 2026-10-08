"""Instalação da skill para agentes de IA (`linkedin-bot --ai ALVO`)."""

from __future__ import annotations

from pathlib import Path

from ..agents import mcp_clients
from ..agents.targets import TARGETS, Target, display_path, install, resolve
from .ui import ARROW, FAIL, OK, WARN, err, out


def _list() -> int:
    out("\nAlvos disponíveis para --ai\n" + "─" * 66)
    kw = max(len(t.key) for t in TARGETS)
    lw = max(len(t.label) for t in TARGETS)
    cwd = Path.cwd()
    for t in TARGETS:
        mark = OK if t.detected else " "
        out(f"  {mark} {t.key.ljust(kw)}  {t.label.ljust(lw)}  [{t.scope}]")
        out(f"    {' ' * kw}  {display_path(t.path(cwd))}")
        client = mcp_clients.client_for(t.key)
        out(f"    {' ' * kw}  MCP: {client.dest() if client else '—'}")
    out("─" * 66)
    out(f"  {OK} = agente detectado nesta máquina\n")
    out("  linkedin-bot --ai claude    instala a skill e registra o MCP de um alvo")
    out("  linkedin-bot --ai all       faz o mesmo em todos os detectados\n")
    return 0


def _install_skill(target: Target) -> None:
    dest, action = install(target)
    out(f"  {OK} {target.label}: skill {action}")
    out(f"    {display_path(dest)}")
    if target.scope == "projeto":
        out(f"    {ARROW} escopo de projeto: vale para {Path.cwd()}")


def _register_mcp(target: Target, client: mcp_clients.Client) -> int:
    """Registra o servidor e relata. Pular não é falha; só um registro tentado que falha."""
    result = client.register(mcp_clients.exe_path())
    label = target.label
    if result.status == mcp_clients.OK:
        suffix = f" ({result.detail})" if result.detail else ""
        out(f"  {OK} {label}: servidor MCP registrado{suffix}")
        if result.path:
            out(f"    {display_path(result.path)}")
        return 0

    if result.status == mcp_clients.SKIPPED:
        out(f"  {WARN} {label}: registro do servidor MCP pulado ({result.detail})")
        emit, rc = out, 0
    else:
        err(f"  {FAIL} {label}: falha ao registrar o servidor MCP ({result.detail})")
        emit, rc = err, 1
    if result.manual:
        emit(f"    {ARROW} registre à mão:")
        for line in result.manual.splitlines():
            emit(f"      {line}")
    return rc


def _install_one(key: str) -> int:
    target = resolve(key)
    if target is None:
        err(f"erro: alvo desconhecido '{key}'")
        err(f"  {ARROW} veja os alvos com: linkedin-bot --ai list")
        return 1

    _install_skill(target)
    client = mcp_clients.client_for(target.key)
    return _register_mcp(target, client) if client else 0


def _install_detected() -> int:
    """Skill nos agentes detectados, MCP nos clientes detectados — duas detecções independentes."""
    rc = 0
    for t in TARGETS:
        client = mcp_clients.client_for(t.key)
        if t.detected:
            _install_skill(t)
        if client and client.detect():
            rc |= _register_mcp(t, client)
    return rc


def run(value: str) -> int:
    value = value.strip().lower()

    if value in ("list", "ls", "lista"):
        return _list()

    out("")
    if value == "all":
        if not any(t.detected for t in TARGETS):
            err("erro: nenhum agente detectado nesta máquina")
            err(f"  {ARROW} instale para um alvo específico: linkedin-bot --ai claude")
            return 1
        rc = _install_detected()
        out("")
        return rc

    rc = _install_one(value)
    if rc == 0:
        out(f"\n  {WARN} reinicie o agente para ele carregar a skill\n")
    return rc
