"""Publish em background: nenhuma tool MCP espera pela rede.

`linkedin_publish_post` dispara um job e responde na hora; o agente acompanha por
`linkedin_publish_status`. O estado de cada job vive em disco
(`<config>/jobs/<job_id>.json`) para que um servidor reiniciado ainda diga a verdade:
"interrompido, nada publicado", "publicado: urn" ou "resultado incerto".

Cada job segura a `PublishLock` do disparo ao fim — a mesma que a CLI usa —, então só
há um publish por vez na máquina. E é a trava, não o PID, que diz se um job ativo no
disco ainda está vivo: o kernel a solta quando o processo morre.

A regra que evita post duplicado: falha antes do POST /posts devolve o preview (pode
tentar de novo); falha ambígua durante o POST deixa o preview consumido e o job
"uncertain" — o usuário confere o perfil.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Callable

from .. import config
from ..auth.session import LinkedInAuth
from ..posts.create import post_url
from ..posts.draft import PROCESSING, PUBLISHED, PUBLISHING, UPLOADING, Draft, Event, publish
from ..posts.lock import PublishLock
from ..transport.errors import LinkedInBotError, PublishInProgressError, definitely_not_created
from .previews import PreviewStore

QUEUED = "queued"
FAILED = "failed"
INTERRUPTED = "interrupted"
UNCERTAIN = "uncertain"

ACTIVE = frozenset({QUEUED, UPLOADING, PROCESSING, PUBLISHING})

CHECK_PROFILE = (
    "Resultado incerto: o post pode ter sido publicado. Confira o perfil antes de tentar "
    "de novo — não gere um novo preview para publicar o mesmo conteúdo."
)
DIED_BEFORE = (
    "O servidor MCP foi encerrado antes de publicar; nada foi publicado. "
    "Gere um novo preview e publique de novo."
)
DIED_DURING = f"O servidor MCP foi encerrado durante a criação do post. {CHECK_PROFILE}"

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class JobRecord:
    job_id: str
    preview_id: str
    visibility: str
    pid: int
    phase: str = QUEUED
    done: int = 0
    total: int = 0
    unit: str = ""
    message: str = ""
    urn: str | None = None
    url: str | None = None
    error: str | None = None
    preview_released: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0


class JobStore:
    """Um arquivo JSON por job, gravado de forma atômica (quem lê nunca vê metade)."""

    def __init__(self, root: Path | None = None, *, now: Callable[[], float] = time.time) -> None:
        self._root = root
        self._now = now

    @property
    def root(self) -> Path:
        return self._root or config.config_dir() / "jobs"

    def now(self) -> float:
        return self._now()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, rec: JobRecord) -> None:
        rec.updated_at = self._now()
        rec.created_at = rec.created_at or rec.updated_at
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{rec.job_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(rec), f, ensure_ascii=False)
            os.replace(tmp, self._path(rec.job_id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, job_id: str) -> JobRecord | None:
        if not _JOB_ID.match(job_id):
            return None
        return self._read(self._path(job_id))

    def _read(self, path: Path) -> JobRecord | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(JobRecord)}
            return JobRecord(**{k: v for k, v in data.items() if k in known})
        except (OSError, ValueError, TypeError):
            return None

    def temp_files(self) -> list[Path]:
        return list(self.root.glob(".*.tmp")) if self.root.is_dir() else []

    def records(self) -> list[tuple[Path, JobRecord | None]]:
        if not self.root.is_dir():
            return []
        return [(p, self._read(p)) for p in self.root.glob("*.json")]


def _describe(exc: BaseException) -> str:
    text = str(exc) if isinstance(exc, LinkedInBotError) else f"{type(exc).__name__}: {exc}"
    notes = getattr(exc, "__notes__", None) or []
    return "\n".join([text, *notes])


Publisher = Callable[..., str]


class JobManager:
    """Dispara e acompanha publishes, um por vez na máquina (via `PublishLock`)."""

    def __init__(
        self,
        previews: PreviewStore,
        store: JobStore | None = None,
        *,
        publisher: Publisher = publish,
        lock: PublishLock | None = None,
        ttl: float = config.JOB_RECORD_TTL,
    ) -> None:
        self.previews = previews
        self.store = store or JobStore()
        self.lock = lock or PublishLock()
        self._publisher = publisher
        self._ttl = ttl
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self.purge()

    # --- consulta ---------------------------------------------------------------

    def _live(self, rec: JobRecord) -> bool:
        """Vivo é quem segura a trava. Dono ilegível conta como vivo: não acusar à toa."""
        holder = self.lock.holder()
        return holder is not None and holder.get("job_id", rec.job_id) == rec.job_id

    def status(self, job_id: str) -> JobRecord | None:
        rec = self.store.load(job_id)
        if rec is None or rec.phase not in ACTIVE or self._live(rec):
            return rec
        # O worker pode ter gravado o fim e saído entre a leitura e a checagem de vida.
        rec = self.store.load(job_id)
        if rec is None or rec.phase not in ACTIVE:
            return rec
        # Ativo no disco, mas o processo que o rodava morreu.
        died_publishing = rec.phase == PUBLISHING
        rec.phase = UNCERTAIN if died_publishing else INTERRUPTED
        rec.error = DIED_DURING if died_publishing else DIED_BEFORE
        self.store.save(rec)
        return rec

    def ensure_idle(self) -> None:
        """Checagem antecipada, antes de gastar o hash; quem garante é o `start`."""
        holder = self.lock.holder()
        if holder is not None:
            raise PublishInProgressError(holder)

    def purge(self) -> None:
        cutoff = self.store.now() - self._ttl
        running = (self.lock.holder() or {}).get("job_id")
        for path, rec in self.store.records():
            if rec is None or (rec.updated_at < cutoff and rec.job_id != running):
                path.unlink(missing_ok=True)
        for tmp in self.store.temp_files():  # restos de um save interrompido
            try:
                if tmp.stat().st_mtime < time.time() - self._ttl:
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # --- disparo ----------------------------------------------------------------

    def start(self, auth: LinkedInAuth, preview_id: str) -> JobRecord:
        job_id = uuid.uuid4().hex
        with self._lock:
            # A trava antes do preview: recusado, o preview continua valendo.
            self.lock.acquire({"origin": "mcp", "job_id": job_id, "pid": os.getpid()})
            try:
                draft = self.previews.take(preview_id)
                rec = JobRecord(
                    job_id=job_id,
                    preview_id=preview_id,
                    visibility=draft.visibility,
                    pid=os.getpid(),
                )
                self.store.save(rec)
                snapshot = replace(rec)  # o job muta `rec` em outra thread
                thread = threading.Thread(
                    target=self._run, args=(rec, auth, draft), name=f"publish-{job_id}", daemon=True
                )
                self._threads[job_id] = thread
                thread.start()
            except BaseException:
                self.lock.release()
                raise
        return snapshot

    def join(self, job_id: str, timeout: float = 10) -> None:
        thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)

    def _run(self, rec: JobRecord, auth: LinkedInAuth, draft: Draft) -> None:
        def on_event(event: Event) -> None:
            rec.phase = event.phase
            if event.phase == UPLOADING:
                rec.done, rec.total, rec.unit = event.done, event.total, event.unit
            if event.phase == PUBLISHED and event.urn:
                # Gravado antes de qualquer outra coisa: é o que impede um "incerto" falso.
                rec.urn, rec.url = event.urn, post_url(event.urn)
            self.store.save(rec)

        def notify(message: str) -> None:
            rec.message = message.strip()
            self.store.save(rec)

        try:
            self._finish(rec, auth, draft, notify, on_event)
        finally:
            self.lock.release()  # só depois do registro final: quem consultar já vê o fim

    def _finish(
        self,
        rec: JobRecord,
        auth: LinkedInAuth,
        draft: Draft,
        notify: Callable[[str], None],
        on_event: Callable[[Event], None],
    ) -> None:
        try:
            urn = self._publisher(auth, draft, notify=notify, on_event=on_event)
        except BaseException as exc:
            if rec.phase == PUBLISHING and not definitely_not_created(exc):
                rec.phase = UNCERTAIN
                rec.error = f"{_describe(exc)}\n{CHECK_PROFILE}"
            else:
                rec.phase = FAILED
                rec.error = _describe(exc)
                self.previews.put(draft)
                rec.preview_released = True
            self.store.save(rec)
            return

        if rec.phase != PUBLISHED or rec.urn != urn:
            rec.phase, rec.urn, rec.url = PUBLISHED, urn, post_url(urn)
            self.store.save(rec)
