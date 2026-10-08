"""doctor: registro do servidor MCP em cada cliente e skills desatualizadas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from linkedin_bot.agents import content, mcp_clients, targets
from linkedin_bot.cli.doctor_cmd import FAIL_, OK_, WARN_, Report, check_mcp, check_skills


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    monkeypatch.setattr(targets, "_home", lambda: home)
    monkeypatch.chdir(proj)
    monkeypatch.setattr(mcp_clients, "_on_path", lambda _binary: False)
    return home


def run(check) -> list[tuple[str, str, str, str]]:
    r = Report()
    check(r)
    return [(c.name, c.status, c.detail, c.hint) for c in r.checks]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def entry(command: str) -> dict:
    return {"mcpServers": {"linkedin-bot": {"command": command, "args": ["mcp"]}}}


# --- MCP ------------------------------------------------------------------------


def test_mcp_nothing_detected(home):
    assert run(check_mcp) == []


def test_mcp_missing_in_detected_client(home):
    (home / ".gemini" / "config").mkdir(parents=True)
    write_json(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {"x": {}}})

    assert run(check_mcp) == [
        ("MCP: Google Antigravity (workspace)", WARN_, "servidor não registrado",
         "rode 'linkedin-bot --ai antigravity'"),
    ]


def test_mcp_registered_and_stale_path(home, tmp_path):
    exe = tmp_path / "linkedin-bot"
    exe.write_text("")
    write_json(home / ".cursor" / "mcp.json", entry(str(exe)))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.linkedin-bot]\ncommand = "/sumiu/linkedin-bot"\nargs = ["mcp"]\n'
    )

    checks = run(check_mcp)

    assert ("MCP: OpenAI Codex", WARN_, "aponta para /sumiu/linkedin-bot, que não existe",
            "rode 'linkedin-bot --ai codex'") in checks
    assert ("MCP", OK_, "registrado em Cursor", "") in checks
    assert all(c[1] != FAIL_ for c in checks)


def test_mcp_claude_code_reads_user_config(home, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_clients, "_on_path", lambda binary: binary == "claude")
    exe = tmp_path / "linkedin-bot"
    exe.write_text("")
    write_json(home / ".claude.json", entry(str(exe)))

    assert run(check_mcp) == [("MCP", OK_, "registrado em Claude Code", "")]


def test_mcp_unreadable_config_is_skipped(home):
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text("{ não é json")
    assert run(check_mcp) == []


def test_mcp_hermes_text_check(home, monkeypatch):
    monkeypatch.setattr(mcp_clients, "_on_path", lambda binary: binary == "hermes")
    (home / ".hermes").mkdir()
    cfg = home / ".hermes" / "config.yaml"
    cfg.write_text("mcp_servers:\n  outro:\n    command: x\n")
    assert run(check_mcp)[0][1] == WARN_

    cfg.write_text("mcp_servers:\n  linkedin-bot:\n    command: /x\n")
    assert run(check_mcp) == [("MCP", OK_, "registrado em Hermes", "")]


# --- skills ---------------------------------------------------------------------


def test_skills_none_installed(home):
    assert run(check_skills) == []


def test_skills_current_and_stale(home):
    claude = targets.BY_KEY["claude"]
    targets.install(claude)
    codex = home / ".codex" / "AGENTS.md"
    codex.parent.mkdir()
    codex.write_text(
        f"# minhas regras\n\n{targets.START}\n# linkedin-bot\nversão antiga\n{targets.END}\n"
    )

    assert run(check_skills) == [
        ("Skills", WARN_, "desatualizada em: OpenAI Codex",
         "rode 'linkedin-bot --ai all' para reinstalar"),
    ]

    targets.install(targets.BY_KEY["codex"])
    assert run(check_skills) == [("Skills", OK_, "atualizada em 2 alvo(s)", "")]


def test_skills_section_without_markers_is_not_ours(home):
    agents = Path.cwd() / "AGENTS.md"
    agents.write_text("# regras do projeto\n")
    assert run(check_skills) == []


def test_skill_body_mentions_the_new_flow():
    body = content.BODY
    assert "linkedin_publish_status" in body
    assert "uncertain" in body
    assert "--video" in body
