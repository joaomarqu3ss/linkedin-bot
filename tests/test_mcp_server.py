"""Servidor MCP em memória, nos dois modos de protocolo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from mcp.client import Client

from linkedin_bot.auth.store import AppCredentials, Token, TokenStore
from linkedin_bot.mcp_server.server import INSTRUCTIONS, build_server, tool_errors
from linkedin_bot.transport.errors import NotConfiguredError

pytestmark = pytest.mark.anyio

MODES = ["auto", "legacy"]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return TokenStore()


def _token(days: float) -> Token:
    now = datetime.now(timezone.utc)
    return Token("tok", now, now + timedelta(days=days, hours=1), ("w_member_social",),
                 "urn:li:person:abc", "Ana")


@pytest.mark.parametrize("mode", MODES)
async def test_instructions(mode):
    async with Client(build_server(), mode=mode) as c:
        assert c.instructions == INSTRUCTIONS


@pytest.mark.parametrize("mode", MODES)
async def test_linkedin_status_annotations(mode):
    async with Client(build_server(), mode=mode) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    a = tools["linkedin_status"].annotations
    assert (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint) == (
        True, False, True, False)


async def _status(mode) -> dict:
    async with Client(build_server(), mode=mode) as c:
        result = await c.call_tool("linkedin_status", {})
    assert not result.is_error
    return result.structured_content


@pytest.mark.parametrize("mode", MODES)
async def test_status_sem_app(mode):
    s = await _status(mode)
    assert s["configured"] is False and s["authenticated"] is False
    assert s["problem"] == "nenhum app LinkedIn configurado"
    assert s["fix"] == "linkedin-bot auth setup"


@pytest.mark.parametrize("mode", MODES)
async def test_status_valido(mode, isolated_config):
    isolated_config.save_app(AppCredentials("cid", "secret"))
    isolated_config.save_token(_token(30))
    s = await _status(mode)
    assert s["authenticated"] is True and s["name"] == "Ana"
    assert s["person_urn"] == "urn:li:person:abc"
    assert s["days_remaining"] == 30 and s["problem"] is None and s["fix"] is None
    datetime.fromisoformat(s["expires_at"])  # ISO 8601
    assert "secret" not in str(s) and "tok" not in s.values()


@pytest.mark.parametrize("mode", MODES)
async def test_status_expirado_e_estado_nao_erro(mode, isolated_config):
    isolated_config.save_app(AppCredentials("cid", "secret"))
    isolated_config.save_token(_token(-2))
    s = await _status(mode)
    assert s["authenticated"] is False
    assert (s["problem"], s["fix"]) == ("token expirado", "linkedin-bot auth login")


async def test_tool_errors_preserva_mensagem():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="linkedin-bot auth setup"):
        with tool_errors():
            raise NotConfiguredError()
