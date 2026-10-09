"""Um publish por vez nesta máquina: CLI e servidores MCP disputam a mesma trava.

A trava é do sistema operacional (`flock` / `msvcrt.locking`) sobre
`<config>/publish.lock`: atômica entre processos, e o kernel a solta quando o processo
morre — não existe trava presa. Ao lado, `publish.lock.holder` diz quem segura, para a
mensagem de recusa apontar o job ou o terminal certo.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import config
from ..transport.errors import PublishInProgressError

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    # flock, e não lockf: dois descritores do mesmo processo também conflitam, então o
    # servidor MCP enxerga o próprio job ocupando a trava.
    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def describe_holder(holder: dict) -> str:
    """Onde está o publish que segura a trava, e o que fazer."""
    if holder.get("origin") == "mcp":
        return (
            f"no servidor MCP (job_id {holder.get('job_id', '?')}). "
            "Acompanhe com linkedin_publish_status e espere ele terminar"
        )
    if holder.get("origin") == "cli":
        return f"num `linkedin-bot post` no terminal (PID {holder.get('pid', '?')}). Espere ele terminar"
    return "em outro publish. Espere ele terminar"


class PublishLock:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path or config.config_dir() / "publish.lock"

    @property
    def _holder_path(self) -> Path:
        return self.path.with_name(self.path.name + ".holder")

    def _open(self) -> int:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)

    def _read_holder(self) -> dict:
        try:
            data = json.loads(self._holder_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_holder(self, holder: dict) -> None:
        target = self._holder_path
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(holder, f)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def acquire(self, holder: dict) -> None:
        """Pega a trava ou falha na hora, dizendo quem a segura."""
        if self._fd is not None:  # este mesmo dono já está publicando
            raise PublishInProgressError(self._read_holder())
        fd = self._open()
        if not _try_lock(fd):
            os.close(fd)
            raise PublishInProgressError(self._read_holder())
        self._fd = fd
        try:
            self._write_holder(holder)
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def holder(self) -> dict | None:
        """Quem segura a trava agora (`{}` se não deu para saber), ou None se está livre."""
        fd = self._open()
        try:
            if _try_lock(fd):
                _unlock(fd)
                return None
            return self._read_holder()
        finally:
            os.close(fd)

    @contextmanager
    def held(self, holder: dict) -> Iterator[None]:
        self.acquire(holder)
        try:
            yield
        finally:
            self.release()
