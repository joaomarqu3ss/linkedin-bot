"""mcp_server/jobs.py: publish em background, estado em disco, liberação do preview."""

from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from linkedin_bot.mcp_server.jobs import JobManager, JobStore
from linkedin_bot.mcp_server.previews import PreviewStore
from linkedin_bot.posts.draft import PUBLISHED, PUBLISHING, UPLOADING, Event, prepare
from linkedin_bot.transport.errors import PostCreationError, UploadFailedError

AUTH = SimpleNamespace(person_urn="urn:li:person:abc")


class Wall:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Publisher:
    """Publisher programável: emite eventos e para num portão até o teste liberar."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.reached = threading.Event()
        self.fail_at: str | None = None
        self.error: BaseException = UploadFailedError("a.mp4", 500, "boom")
        self.calls = 0

    def __call__(self, auth, draft, *, notify, on_event):
        self.calls += 1
        on_event(Event(UPLOADING, 1, 2, "bytes"))
        notify("  → enviando")
        if self.fail_at == UPLOADING:
            raise self.error
        self.reached.set()
        self.gate.wait(5)
        on_event(Event(PUBLISHING))
        if self.fail_at == PUBLISHING:
            raise self.error
        on_event(Event(PUBLISHED, urn="urn:li:share:9"))
        return "urn:li:share:9"


@pytest.fixture
def wall():
    return Wall()


@pytest.fixture
def store(tmp_path, wall):
    return JobStore(tmp_path / "jobs", now=wall)


@pytest.fixture
def previews():
    return PreviewStore()


@pytest.fixture
def publisher():
    return Publisher()


@pytest.fixture
def manager(store, previews, publisher):
    return JobManager(previews, store, publisher=publisher)


def started(manager, previews, text="olá"):
    pid = previews.put(prepare(text))
    rec = manager.start(AUTH, pid)
    return pid, rec


def test_publish_runs_in_background_and_persists(manager, previews, store):
    pid, rec = started(manager, previews)
    manager.join(rec.job_id)

    done = manager.status(rec.job_id)
    assert done.phase == PUBLISHED
    assert done.urn == "urn:li:share:9"
    assert done.url == "https://www.linkedin.com/feed/update/urn:li:share:9/"
    assert done.preview_id == pid and done.visibility == "PUBLIC"
    assert previews.get(pid) is None  # consumido
    on_disk = json.loads((store.root / f"{rec.job_id}.json").read_text())
    assert on_disk["urn"] == "urn:li:share:9"


def test_status_shows_progress_while_running(manager, previews, publisher):
    publisher.gate.clear()
    _, rec = started(manager, previews)
    assert publisher.reached.wait(5)

    live = manager.status(rec.job_id)
    assert (live.phase, live.done, live.total, live.unit) == (UPLOADING, 1, 2, "bytes")
    assert live.message == "→ enviando"

    publisher.gate.set()
    manager.join(rec.job_id)


def test_one_job_at_a_time(manager, previews, publisher):
    publisher.gate.clear()
    _, first = started(manager, previews, "um")
    assert publisher.reached.wait(5)

    second = previews.put(prepare("dois"))
    with pytest.raises(ToolError, match=first.job_id):
        manager.start(AUTH, second)
    assert previews.get(second) is not None  # recusado sem consumir

    publisher.gate.set()
    manager.join(first.job_id)
    manager.join(manager.start(AUTH, second).job_id)
    assert publisher.calls == 2


def test_failure_before_publishing_releases_the_preview(manager, previews, publisher):
    publisher.fail_at = UPLOADING
    pid, rec = started(manager, previews)
    manager.join(rec.job_id)

    failed = manager.status(rec.job_id)
    assert failed.phase == "failed"
    assert "Falha ao enviar a.mp4" in failed.error
    assert failed.preview_released
    assert previews.get(pid) is not None


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("rede caiu"), PostCreationError(503, "indisponível")],
)
def test_ambiguous_failure_while_publishing_is_uncertain(manager, previews, publisher, error):
    publisher.fail_at, publisher.error = PUBLISHING, error
    pid, rec = started(manager, previews)
    manager.join(rec.job_id)

    st = manager.status(rec.job_id)
    assert st.phase == "uncertain"
    assert "confira o perfil" in st.error.lower()
    assert not st.preview_released
    assert previews.get(pid) is None


def test_definitive_refusal_while_publishing_releases(manager, previews, publisher):
    publisher.fail_at, publisher.error = PUBLISHING, PostCreationError(422, "texto inválido")
    pid, rec = started(manager, previews)
    manager.join(rec.job_id)

    st = manager.status(rec.job_id)
    assert st.phase == "failed" and st.preview_released
    assert previews.get(pid) is not None


def _orphan(store, phase, pid=999_999):
    from linkedin_bot.mcp_server.jobs import JobRecord

    rec = JobRecord(
        job_id="a" * 32, preview_id="p", visibility="PUBLIC", pid=pid,
        instance="outro-processo", phase=phase,
    )
    store.save(rec)
    return rec


@pytest.mark.parametrize(
    "phase, expected, words",
    [
        (UPLOADING, "interrupted", "nada foi publicado"),
        ("processing", "interrupted", "nada foi publicado"),
        (PUBLISHING, "uncertain", "confira o perfil"),
    ],
)
def test_job_of_a_dead_process(store, previews, phase, expected, words):
    _orphan(store, phase)
    fresh = JobManager(previews, store, pid_alive=lambda _pid: False)

    st = fresh.status("a" * 32)
    assert st.phase == expected and words in st.error.lower()
    assert store.load("a" * 32).phase == expected  # resolvido e gravado


def test_job_of_another_live_process_stays_active(store, previews):
    _orphan(store, UPLOADING)
    other = JobManager(previews, store, pid_alive=lambda _pid: True)

    assert other.status("a" * 32).phase == UPLOADING
    with pytest.raises(ToolError, match="a" * 32):
        other.start(AUTH, previews.put(prepare("x")))


def test_unknown_or_malformed_job_id(manager):
    assert manager.status("f" * 32) is None
    assert manager.status("../../credentials") is None


def test_purge_drops_records_older_than_4h(store, previews, wall):
    from linkedin_bot.mcp_server.jobs import JobRecord

    def rec(job_id, phase, pid=999_999):
        store.save(JobRecord(job_id=job_id, preview_id="p", visibility="PUBLIC", pid=pid,
                             instance="x", phase=phase))

    rec("1" * 32, PUBLISHED)
    rec("2" * 32, "uncertain")
    rec("3" * 32, UPLOADING)          # "ativo" há mais de 4 h: o PID foi reaproveitado
    (store.root / "lixo.json").write_text("{")
    wall.t += 4 * 3600 + 1
    rec("4" * 32, PUBLISHED)          # recente: fica

    JobManager(previews, store, pid_alive=lambda pid: True)

    rec("5" * 32, UPLOADING)          # ativo recente de processo vivo: fica
    JobManager(previews, store, pid_alive=lambda pid: True)
    assert sorted(p.stem for p in store.root.glob("*.json")) == ["4" * 32, "5" * 32]


def test_status_does_not_overwrite_a_job_that_just_finished(store, previews, monkeypatch):
    """O worker grava `published` e sai entre a leitura e a checagem de vida."""
    manager = JobManager(previews, store, publisher=Publisher())
    pid, rec = started(manager, previews)
    manager.join(rec.job_id)
    stale = store.load(rec.job_id)
    stale.phase = UPLOADING  # a leitura antiga, de antes do fim

    real_load = store.load
    reads = iter([stale])
    monkeypatch.setattr(store, "load", lambda job_id: next(reads, None) or real_load(job_id))

    assert manager.status(rec.job_id).phase == PUBLISHED
    assert real_load(rec.job_id).phase == PUBLISHED


def test_active_record_older_than_ttl_is_dead_even_if_pid_exists(store, previews, wall):
    # PID reaproveitado por outro processo: um upload não dura 4 h
    _orphan(store, UPLOADING)
    wall.t += 4 * 3600 + 1
    manager = JobManager(previews, store, pid_alive=lambda _pid: True)
    assert manager.busy() is None


def test_purge_drops_stale_temp_files(store, previews, wall):
    store.root.mkdir(parents=True)
    tmp = store.root / ".abc.x.tmp"
    tmp.write_text("{")
    os.utime(tmp, (0, 0))
    JobManager(previews, store)
    assert not tmp.exists()
