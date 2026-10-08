"""`--ai`: registro do servidor MCP em cada cliente e skill que prefere as tools."""

from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from linkedin_bot.agents import content, targets
from linkedin_bot.cli.__main__ import main

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="CLIs falsas são scripts sh")

BLOCK_START = "# linkedin-bot:start"
BLOCK_END = "# linkedin-bot:end"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """HOME e cwd isolados; PATH só com um `linkedin-bot` falso, sem claude nem hermes."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    bin_dir = tmp_path / "bin"
    for d in (home, proj, bin_dir):
        d.mkdir()
    exe = bin_dir / "linkedin-bot"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)

    monkeypatch.setattr(targets, "_home", lambda: home)
    monkeypatch.chdir(proj)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    class Env:
        pass

    e = Env()
    e.home, e.proj, e.bin, e.exe, e.tmp = home, proj, bin_dir, exe, tmp_path
    return e


def fake_cli(env, name: str, rc_for: dict[str, int] | None = None) -> Path:
    """Instala `name` em env.bin: anexa os argumentos a um log, uma invocação por linha."""
    log = env.tmp / f"{name}.log"
    rc_for = rc_for or {}
    cases = "".join(f'  "{sub}"*) exit {rc} ;;\n' for sub, rc in rc_for.items())
    script = env.bin / name
    script.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log}"\ncase "$*" in\n{cases}esac\nexit 0\n'
    )
    script.chmod(0o755)
    return log


def read_log(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def snapshot(*roots: Path) -> dict[str, bytes]:
    return {
        str(p): p.read_bytes() for r in roots for p in sorted(r.rglob("*")) if p.is_file()
    }


# --- skill ------------------------------------------------------------------


def test_skill_body_prefers_mcp_tools():
    assert "linkedin_preview_post" in content.BODY
    assert "linkedin_publish_post" in content.BODY
    # a CLI continua documentada como fallback
    assert "--dry-run" in content.BODY


def test_versioned_copies_match_skill_body():
    root = Path(__file__).resolve().parent.parent
    assert (root / ".agents/rules/linkedin-bot.md").read_text() == content.plain()
    assert (root / ".cursor/rules/linkedin-bot.mdc").read_text() == content.mdc()


# --- claude -----------------------------------------------------------------


@posix_only
def test_claude_removes_then_adds_with_absolute_path(env, capsys):
    log = fake_cli(env, "claude")
    assert main(["--ai", "claude"]) == 0
    assert read_log(log) == [
        "mcp remove -s user linkedin-bot",
        f"mcp add -s user linkedin-bot -- {env.exe} mcp",
    ]
    assert "servidor MCP registrado" in capsys.readouterr().out


@posix_only
def test_claude_remove_failure_is_ignored(env):
    log = fake_cli(env, "claude", rc_for={"mcp remove": 1})
    assert main(["--ai", "claude"]) == 0
    assert read_log(log)[-1].startswith("mcp add")


@posix_only
def test_claude_add_failure_exits_1(env, capsys):
    fake_cli(env, "claude", rc_for={"mcp add": 1})
    assert main(["--ai", "claude"]) == 1
    assert "linkedin-bot" in capsys.readouterr().err


def test_claude_without_binary_prints_manual_command(env, capsys):
    assert main(["--ai", "claude"]) == 0
    out = capsys.readouterr().out
    assert f"claude mcp add -s user linkedin-bot -- {env.exe} mcp" in out


# --- codex ------------------------------------------------------------------


def codex_config(env) -> Path:
    return env.home / ".codex" / "config.toml"


def test_codex_creates_file_with_block(env):
    assert main(["--ai", "codex"]) == 0
    text = codex_config(env).read_text()
    server = tomllib.loads(text)["mcp_servers"]["linkedin-bot"]
    assert server["command"] == str(env.exe)
    assert server["args"] == ["mcp"]
    assert server["tool_timeout_sec"] == 300
    assert server["tools"]["linkedin_publish_post"]["approval_mode"] == "prompt"
    assert text.count(BLOCK_START) == 1 and text.count(BLOCK_END) == 1


def test_codex_twice_leaves_one_block(env):
    main(["--ai", "codex"])
    first = codex_config(env).read_bytes()
    main(["--ai", "codex"])
    assert codex_config(env).read_bytes() == first


def test_codex_preserves_content_around_block_byte_for_byte(env):
    path = codex_config(env)
    path.parent.mkdir()
    before = 'model = "o3"\r\n\r\n[profiles.fast]\nmodel = "mini"\n'
    after = '\n[mcp_servers.other]\ncommand = "x"\n'
    old_block = f'{BLOCK_START}\n[mcp_servers.linkedin-bot]\ncommand = "/old"\n{BLOCK_END}\n'
    path.write_bytes((before + old_block + after).encode())

    assert main(["--ai", "codex"]) == 0
    text = path.read_bytes().decode()
    assert text.startswith(before) and text.endswith(after)
    assert tomllib.loads(text)["mcp_servers"]["linkedin-bot"]["command"] == str(env.exe)


def test_codex_appends_to_existing_file(env):
    path = codex_config(env)
    path.parent.mkdir()
    original = 'model = "o3"'
    path.write_text(original)
    assert main(["--ai", "codex"]) == 0
    text = path.read_text()
    assert text.startswith(original)
    assert "linkedin-bot" in tomllib.loads(text)["mcp_servers"]


def test_codex_escapes_windows_style_paths(env):
    from linkedin_bot.agents import mcp_clients

    block = mcp_clients.codex_block(r"C:\Users\a b\linkedin-bot.exe")
    parsed = tomllib.loads(block)
    assert parsed["mcp_servers"]["linkedin-bot"]["command"] == r"C:\Users\a b\linkedin-bot.exe"


def test_codex_user_table_outside_markers_is_left_intact(env, capsys):
    path = codex_config(env)
    path.parent.mkdir()
    original = '[mcp_servers.linkedin-bot]\ncommand = "/mine"\n'
    path.write_text(original)
    assert main(["--ai", "codex"]) == 0
    assert path.read_text() == original
    assert "[mcp_servers.linkedin-bot]" in capsys.readouterr().out


def test_codex_invalid_toml_is_left_intact(env, capsys):
    path = codex_config(env)
    path.parent.mkdir()
    original = "this is = = not toml\n"
    path.write_text(original)
    assert main(["--ai", "codex"]) == 0
    assert path.read_text() == original
    assert BLOCK_START in capsys.readouterr().out


# --- JSON: gemini, antigravity, cursor --------------------------------------

JSON_TARGETS = [
    ("gemini", lambda e: e.home / ".gemini" / "settings.json", {}),
    ("antigravity", lambda e: e.home / ".gemini" / "config" / "mcp_config.json", {}),
    ("cursor", lambda e: e.home / ".cursor" / "mcp.json", {"type": "stdio"}),
]


@pytest.mark.parametrize("key,where,extra", JSON_TARGETS)
def test_json_creates_file(env, key, where, extra):
    assert main(["--ai", key]) == 0
    data = json.loads(where(env).read_text())
    assert data["mcpServers"]["linkedin-bot"] == {
        **extra,
        "command": str(env.exe),
        "args": ["mcp"],
    }


@pytest.mark.parametrize("key,where,extra", JSON_TARGETS)
def test_json_preserves_other_servers_and_keys(env, key, where, extra):
    path = where(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"theme": "ñ", "mcpServers": {"other": {"command": "x"}}}, ensure_ascii=False)
    )
    assert main(["--ai", key]) == 0
    data = json.loads(path.read_text())
    assert data["theme"] == "ñ"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert "linkedin-bot" in data["mcpServers"]
    assert path.read_text().endswith("}\n")
    assert "ñ" in path.read_text()  # ensure_ascii=False


@pytest.mark.parametrize("key,where,extra", JSON_TARGETS)
def test_json_twice_is_identical(env, key, where, extra):
    main(["--ai", key])
    first = where(env).read_bytes()
    main(["--ai", key])
    assert where(env).read_bytes() == first


@pytest.mark.parametrize("key,where,extra", JSON_TARGETS)
def test_json_invalid_is_left_intact(env, key, where, extra, capsys):
    path = where(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    assert main(["--ai", key]) == 0
    assert path.read_text() == "{ not json"
    assert '"mcpServers"' in capsys.readouterr().out


@posix_only
@pytest.mark.parametrize("key,where,extra", JSON_TARGETS)
def test_json_preserves_file_mode(env, key, where, extra):
    path = where(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    path.chmod(0o600)
    assert main(["--ai", key]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "linkedin-bot" in json.loads(path.read_text())["mcpServers"]


# --- hermes -----------------------------------------------------------------


@posix_only
def test_hermes_without_tty_skips_with_manual_command(env, capsys):
    log = fake_cli(env, "hermes")
    assert main(["--ai", "hermes"]) == 0
    assert read_log(log) == []
    assert f"hermes mcp add linkedin-bot --command {env.exe} --args mcp" in capsys.readouterr().out


@posix_only
def test_hermes_with_tty_runs_remove_then_add(env, monkeypatch):
    log = fake_cli(env, "hermes")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert main(["--ai", "hermes"]) == 0
    assert read_log(log) == [
        "mcp remove linkedin-bot",
        f"mcp add linkedin-bot --command {env.exe} --args mcp",
    ]


def test_hermes_without_binary_prints_manual_command(env, capsys):
    assert main(["--ai", "hermes"]) == 0
    assert "hermes mcp add linkedin-bot" in capsys.readouterr().out


def test_agents_target_has_no_mcp(env, capsys):
    assert main(["--ai", "agents"]) == 0
    assert "MCP" not in capsys.readouterr().out


# --- --ai all / list --------------------------------------------------------


@posix_only
def test_all_twice_produces_identical_files(env):
    fake_cli(env, "claude")
    fake_cli(env, "hermes")
    (env.home / ".claude").mkdir()
    (env.home / ".codex").mkdir()
    (env.home / ".gemini" / "config").mkdir(parents=True)
    (env.home / ".cursor").mkdir()

    assert main(["--ai", "all"]) == 0
    first = snapshot(env.home, env.proj)
    assert main(["--ai", "all"]) == 0
    assert snapshot(env.home, env.proj) == first

    assert (env.home / ".codex" / "config.toml").exists()
    assert (env.home / ".cursor" / "mcp.json").exists()
    assert (env.home / ".gemini" / "config" / "mcp_config.json").exists()


def test_all_registers_only_where_client_is_detected(env):
    (env.home / ".claude").mkdir()  # só a skill do claude; sem binário, sem nada de MCP
    assert main(["--ai", "all"]) == 0
    assert not (env.home / ".codex").exists()
    assert not (env.home / ".gemini" / "settings.json").exists()
    assert not (env.home / ".cursor" / "mcp.json").exists()


def test_all_detects_mcp_independently_of_skill(env):
    # ~/.gemini/settings.json existe: o cliente MCP do gemini é detectado
    (env.home / ".gemini").mkdir()
    (env.home / ".gemini" / "settings.json").write_text("{}")
    assert main(["--ai", "all"]) == 0
    data = json.loads((env.home / ".gemini" / "settings.json").read_text())
    assert "linkedin-bot" in data["mcpServers"]


def test_list_shows_mcp_destination(env, capsys):
    assert main(["--ai", "list"]) == 0
    out = capsys.readouterr().out
    assert "config.toml" in out
    assert "mcp_config.json" in out
    assert "mcp.json" in out
    agents_block = out.split("agents")[-1]
    assert "MCP: —" in agents_block


# --- claude desktop ---------------------------------------------------------


def desktop_config(env) -> Path:
    base = {
        "darwin": env.home / "Library" / "Application Support" / "Claude",
        "win32": Path(os.environ.get("APPDATA", env.home / "AppData" / "Roaming")) / "Claude",
    }.get(sys.platform, env.home / ".config" / "Claude")
    return base / "claude_desktop_config.json"


def test_claude_desktop_creates_file_without_skill(env):
    assert main(["--ai", "claude-desktop"]) == 0
    data = json.loads(desktop_config(env).read_text())
    assert data["mcpServers"]["linkedin-bot"] == {"command": str(env.exe), "args": ["mcp"]}
    assert not (env.home / ".claude").exists()  # alvo só de MCP: nenhuma skill


def test_claude_desktop_preserves_app_state(env):
    path = desktop_config(env)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"o": {"command": "y"}}}))
    assert main(["--ai", "claude-desktop"]) == 0
    data = json.loads(path.read_text())
    assert data["preferences"] == {"x": 1}
    assert data["mcpServers"]["o"] == {"command": "y"}
    assert "linkedin-bot" in data["mcpServers"]


def test_all_registers_claude_desktop_when_detected(env):
    desktop_config(env).parent.mkdir(parents=True)
    assert main(["--ai", "all"]) == 0
    assert "linkedin-bot" in json.loads(desktop_config(env).read_text())["mcpServers"]


def test_list_shows_claude_desktop(env, capsys):
    assert main(["--ai", "list"]) == 0
    assert "claude_desktop_config.json" in capsys.readouterr().out
