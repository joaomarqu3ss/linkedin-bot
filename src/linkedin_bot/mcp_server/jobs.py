"""Publish em background: nenhuma tool MCP espera pela rede.

`linkedin_publish_post` dispara um job e responde na hora; o agente acompanha por
`linkedin_publish_status`. O estado de cada job vive em disco
(`<config>/jobs/<job_id>.json`) para que um servidor reiniciado ainda diga a verdade:
"interrompido, nada publicado", "publicado: urn" ou "resultado incerto".

A regra que evita post duplicado: falha antes do POST /posts devolve o preview (pode
tentar de novo); falha ambígua durante o POST deixa o preview consumido e o job
"uncertain" — o usuário confere o perfil.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

from .. import config
from ..posts.create import post_url
from ..auth.session import LinkedInAuth
from ..posts.draft import PROCESSING, PUBLISHED, PUBLISHING, UPLOADING, Draft, Event, publish
from ..transport.errors import LinkedInBotError, definitely_not_created
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
    instance: str
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


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) no Windows não testa: 0 é CTRL_C_EVENT.
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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
    """Dispara e acompanha publishes, um por vez.

    No processo, a garantia vem de um lock. Entre processos (dois clientes de IA, cada
    um com seu servidor) vem da leitura dos registros em disco — sem trava de arquivo,
    então dois disparos no mesmo instante ainda passariam.
    """

    def __init__(
        self,
        previews: PreviewStore,
        store: JobStore | None = None,
        *,
        publisher: Publisher = publish,
        pid_alive: Callable[[int], bool] = pid_alive,
        ttl: float = config.JOB_RECORD_TTL,
    ) -> None:
        self.previews = previews
        self.store = store or JobStore()
        self._publisher = publisher
        self._pid_alive = pid_alive
        self._ttl = ttl
        self.instance = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self.purge()

    # --- consulta ---------------------------------------------------------------

    def _live(self, rec: JobRecord) -> bool:
        if rec.updated_at < self.store.now() - self._ttl:
            return False  # nenhum publish dura isso: o PID foi reaproveitado
        if rec.instance == self.instance:
            thread = self._threads.get(rec.job_id)
            return thread is not None and thread.is_alive()
        return rec.pid != os.getpid() and self._pid_alive(rec.pid)

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

    def busy(self) -> JobRecord | None:
        for _, rec in self.store.records():
            if rec and rec.phase in ACTIVE and self._live(rec):
                return rec
        return None

    def ensure_idle(self) -> None:
        running = self.busy()
        if running:
            raise ToolError(
                f"já existe um publish em andamento (job_id {running.job_id}); "
                "acompanhe com linkedin_publish_status antes de publicar outro"
            )

    def purge(self) -> None:
        cutoff = self.store.now() - self._ttl
        for path, rec in self.store.records():
            if rec is None or (rec.updated_at < cutoff and not (
                rec.phase in ACTIVE and self._live(rec)
            )):
                path.unlink(missing_ok=True)
        for tmp in self.store.temp_files():  # restos de um save interrompido
            try:
                if tmp.stat().st_mtime < time.time() - self._ttl:
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # --- disparo ----------------------------------------------------------------

    def start(self, auth: LinkedInAuth, preview_id: str) -> JobRecord:
        with self._lock:
            self.ensure_idle()
            draft = self.previews.take(preview_id)
            rec = JobRecord(
                job_id=uuid.uuid4().hex,
                preview_id=preview_id,
                visibility=draft.visibility,
                pid=os.getpid(),
                instance=self.instance,
            )
            self.store.save(rec)
            snapshot = replace(rec)  # o job muta `rec` em outra thread
            thread = threading.Thread(
                target=self._run, args=(rec, auth, draft), name=f"publish-{rec.job_id}", daemon=True
            )
            self._threads[rec.job_id] = thread
            thread.start()
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
