"""Smoke test do processo real: nada além do protocolo pode sair no stdout."""

from __future__ import annotations

import os
import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.anyio


async def test_processo_real(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "linkedin_bot.cli", "mcp"],
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path)},
    )
    async with Client(params) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        result = await c.call_tool("linkedin_status", {})
    assert sorted(names) == [
        "linkedin_preview_post", "linkedin_publish_post", "linkedin_publish_status", "linkedin_status"
    ]
    assert result.structured_content["configured"] is False
