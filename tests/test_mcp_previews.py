"""PreviewStore: uso único, TTL e id determinístico."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from linkedin_bot.mcp_server.previews import PreviewStore
from linkedin_bot.posts.draft import prepare


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return PreviewStore(ttl=900, now=clock)


def test_put_usa_o_fingerprint_como_id(store):
    draft = prepare("olá")
    assert store.put(draft) == draft.fingerprint
    assert store.put(prepare("olá")) == draft.fingerprint


def test_get_nao_consome(store):
    draft = prepare("olá")
    pid = store.put(draft)
    assert store.get(pid) == draft
    assert store.get(pid) == draft


def test_get_desconhecido_devolve_none(store):
    assert store.get("nope") is None


def test_take_consome(store):
    draft = prepare("olá")
    pid = store.put(draft)
    assert store.take(pid) == draft
    assert store.get(pid) is None
    with pytest.raises(ToolError, match="preview desconhecido ou expirado"):
        store.take(pid)


def test_take_desconhecido(store):
    with pytest.raises(ToolError, match="chame linkedin_preview_post de novo"):
        store.take("nope")


def test_expira_apos_o_ttl(store, clock):
    pid = store.put(prepare("olá"))
    clock.t += 899
    assert store.get(pid) is not None
    clock.t += 2
    assert store.get(pid) is None
    with pytest.raises(ToolError, match="desconhecido ou expirado"):
        store.take(pid)


def test_novo_preview_renova_o_prazo(store, clock):
    draft = prepare("olá")
    pid = store.put(draft)
    clock.t += 800
    assert store.put(draft) == pid
    clock.t += 800
    assert store.get(pid) == draft


def test_expirados_sao_descartados(store, clock):
    store.put(prepare("um"))
    clock.t += 1000
    store.put(prepare("dois"))
    assert len(store) == 1
