"""posts/lock.py: um publish por vez na máquina, entre CLI e servidores MCP."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from linkedin_bot.posts.lock import PublishLock, describe_holder
from linkedin_bot.transport.errors import PublishInProgressError


@pytest.fixture
def path(tmp_path):
    return tmp_path / "publish.lock"


def test_acquire_release(path):
    lock = PublishLock(path)
    assert lock.holder() is None
    lock.acquire({"origin": "cli"})
    lock.release()
    assert lock.holder() is None
    lock.acquire({"origin": "cli"})  # pode pegar de novo
    lock.release()


def test_second_holder_is_refused_and_told_who_holds(path):
    first, second = PublishLock(path), PublishLock(path)
    first.acquire({"origin": "mcp", "job_id": "a" * 32, "pid": 123})

    with pytest.raises(PublishInProgressError, match="a" * 32) as exc:
        second.acquire({"origin": "cli"})
    assert exc.value.holder["origin"] == "mcp"
    assert second.holder() == {"origin": "mcp", "job_id": "a" * 32, "pid": 123}
    # quem segura também se vê ocupado: a sondagem usa outro descritor
    assert first.holder() is not None

    first.release()
    second.acquire({"origin": "cli"})
    second.release()


def test_context_manager_releases_on_error(path):
    lock = PublishLock(path)
    with pytest.raises(KeyboardInterrupt):
        with lock.held({"origin": "cli"}):
            raise KeyboardInterrupt
    assert lock.holder() is None


def test_lock_dies_with_the_process(path):
    """A trava é do kernel: processo morto não deixa trava presa."""
    script = textwrap.dedent(
        f"""
        import os, sys
        from linkedin_bot.posts.lock import PublishLock
        lock = PublishLock({str(path)!r})
        lock.acquire({{"origin": "cli", "pid": os.getpid()}})
        print("ok", flush=True)
        sys.stdin.read()
        os._exit(0)  # sai sem release
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "ok"
        mine = PublishLock(path)
        with pytest.raises(PublishInProgressError, match=f"PID {proc.pid}"):
            mine.acquire({"origin": "cli"})
    finally:
        proc.stdin.close()
        proc.wait(10)

    mine.acquire({"origin": "cli"})
    mine.release()


@pytest.mark.parametrize(
    "holder, words",
    [
        ({"origin": "mcp", "job_id": "b" * 32}, ["servidor MCP", "b" * 32, "linkedin_publish_status"]),
        ({"origin": "cli", "pid": 77}, ["linkedin-bot post", "PID 77"]),
        ({}, ["outro publish"]),
    ],
)
def test_describe_holder(holder, words):
    text = describe_holder(holder)
    for w in words:
        assert w in text

