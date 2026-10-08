"""Upload de vídeo: initializeUpload → PUT de cada parte → finalizeUpload → processamento.

Diferente da imagem, o vídeo sobe em partes (a LinkedIn devolve uma `uploadUrl` por
faixa de ~4 MB) e só fica utilizável depois de um processamento assíncrono.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .. import config
from ..auth.session import LinkedInAuth
from ..transport.errors import (
    CorruptVideoError,
    UnsupportedVideoFormatError,
    UploadFailedError,
    VideoDurationError,
    VideoNotFoundError,
    VideoProcessingFailedError,
    VideoProcessingTimeoutError,
    VideoSizeError,
)
from .mp4 import probe

SUFFIX = ".mp4"
MIN_BYTES = 75 * 1024
MAX_BYTES = 500 * 1024 * 1024
MIN_SECONDS = 3
MAX_SECONDS = 30 * 60

UNKNOWN_DURATION_WARNING = (
    f"não consegui verificar a duração do vídeo (limite: {MIN_SECONDS} s a "
    f"{MAX_SECONDS // 60} min)"
)

Notify = Callable[[str], None]
OnBytes = Callable[[int, int], None]


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    size_bytes: int
    duration: float | None = None

    def describe(self) -> str:
        if self.duration is None:
            length = "duração ?"
        else:
            minutes, seconds = divmod(round(self.duration), 60)
            length = f"{minutes}:{seconds:02d}"
        return f"{self.path.name} ({length}, {self.size_bytes / 1024 / 1024:.1f} MB)"


def inspect_video(raw_path: str | Path) -> VideoInfo:
    """Valida formato, integridade, tamanho e duração antes de gastar upload.

    Duração ilegível num MP4 íntegro (ex.: fragmentado) não bloqueia: vira aviso.
    """
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise VideoNotFoundError(str(path))

    suffix = path.suffix.lower()
    if suffix != SUFFIX:
        raise UnsupportedVideoFormatError(str(path), suffix)

    try:
        found = probe(path)
        size = path.stat().st_size
    except OSError as exc:
        raise CorruptVideoError(
            str(path), f"não foi possível ler o arquivo ({exc.strerror or exc})"
        ) from None
    if found is None:
        raise UnsupportedVideoFormatError(str(path), f"{suffix} sem assinatura MP4")
    if not found.has_moov:
        raise CorruptVideoError(str(path), "arquivo incompleto ou corrompido (sem o bloco moov)")

    if not MIN_BYTES <= size <= MAX_BYTES:
        raise VideoSizeError(str(path), size, MIN_BYTES, MAX_BYTES)

    if found.duration is not None and not MIN_SECONDS <= found.duration <= MAX_SECONDS:
        raise VideoDurationError(str(path), found.duration, MIN_SECONDS, MAX_SECONDS)

    return VideoInfo(path=path, size_bytes=size, duration=found.duration)


# --- upload -------------------------------------------------------------------


@dataclass(frozen=True)
class UploadPlan:
    video: str
    token: str
    parts: tuple[tuple[int, int, str], ...]  # (firstByte, lastByte, uploadUrl)


def initialize_upload(auth: LinkedInAuth, info: VideoInfo) -> UploadPlan:
    with auth.api_client() as client:
        r = client.post(
            "/videos?action=initializeUpload",
            json={
                "initializeUploadRequest": {
                    "owner": auth.person_urn,
                    "fileSizeBytes": info.size_bytes,
                    "uploadCaptions": False,
                    "uploadThumbnail": False,
                }
            },
        )
        if r.status_code != 200:
            raise UploadFailedError("initializeUpload", r.status_code, r.text)
        value = r.json()["value"]
    parts = tuple(
        (int(p["firstByte"]), int(p["lastByte"]), p["uploadUrl"])
        for p in value["uploadInstructions"]
    )
    return UploadPlan(video=value["video"], token=value.get("uploadToken", ""), parts=parts)


def upload_parts(
    auth: LinkedInAuth, info: VideoInfo, plan: UploadPlan, *, on_bytes: OnBytes
) -> list[str]:
    """Envia as partes em ordem e devolve os ETags.

    Cada parte vai como `bytes` (~4 MB na memória), não como stream: assim a
    requisição é reproduzível e o RetryTransport cobre falhas de rede e 5xx.
    """
    etags: list[str] = []
    sent = 0
    with auth.upload_client() as client, info.path.open("rb") as fh:
        for first, last, url in plan.parts:
            fh.seek(first)
            chunk = fh.read(last - first + 1)
            r = client.put(url, content=chunk, headers={"Content-Type": "application/octet-stream"})
            if r.status_code not in (200, 201):
                raise UploadFailedError(str(info.path), r.status_code, r.text)
            etag = r.headers.get("etag")
            if not etag:
                raise UploadFailedError(str(info.path), r.status_code, "resposta sem ETag")
            etags.append(etag.strip('"'))
            sent += len(chunk)
            on_bytes(sent, info.size_bytes)
    return etags


def finalize_upload(auth: LinkedInAuth, plan: UploadPlan, etags: list[str]) -> None:
    with auth.api_client() as client:
        r = client.post(
            "/videos?action=finalizeUpload",
            json={
                "finalizeUploadRequest": {
                    "video": plan.video,
                    "uploadToken": plan.token,
                    "uploadedPartIds": etags,
                }
            },
        )
        if r.status_code != 200:
            raise UploadFailedError("finalizeUpload", r.status_code, r.text)


def upload_video(
    auth: LinkedInAuth,
    info: VideoInfo,
    *,
    notify: Notify = lambda _: None,
    on_bytes: OnBytes = lambda *_: None,
) -> str:
    """Sobe um vídeo já inspecionado e devolve o `urn:li:video:{id}` (ainda processando)."""
    notify(f"  → registrando upload de {info.describe()}")
    plan = initialize_upload(auth, info)
    try:
        etags = upload_parts(auth, info, plan, on_bytes=on_bytes)
        finalize_upload(auth, plan, etags)
    except Exception as exc:
        exc.add_note(f"o vídeo registrado ficou sem uso na LinkedIn: {plan.video}")
        raise
    notify(f"  ✓ {plan.video} enviado")
    return plan.video


# --- processamento --------------------------------------------------------------


UNREADABLE = None   # o token não lê o asset (403)
TRANSIENT = ""      # erro passageiro: segue esperando


def check_status(auth: LinkedInAuth, video_urn: str) -> tuple[str | None, str | None]:
    """(status, motivo da falha). UNREADABLE com 403; TRANSIENT com outros erros."""
    with auth.api_client() as client:
        r = client.get(f"/videos/{quote(video_urn, safe='')}")
        if r.status_code == 403:
            return UNREADABLE, None
        if r.status_code != 200:
            return TRANSIENT, None
        body = r.json()
        return body.get("status"), body.get("processingFailureReason")


def wait_processed(
    auth: LinkedInAuth,
    video_urn: str,
    *,
    notify: Notify = lambda _: None,
    max_wait: float = config.VIDEO_PROCESSING_MAX,
    interval: float = config.VIDEO_STATUS_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Espera o vídeo ficar AVAILABLE. Devolve None se o status não for legível.

    Com status ilegível quem espera é a criação do post (`create_post_when_ready`).
    """
    start = clock()
    while True:
        status, reason = check_status(auth, video_urn)
        if status is UNREADABLE:
            notify("  (status do vídeo não legível com este escopo — a criação do post vai esperar)")
            return None
        if status == "AVAILABLE":
            return status
        if status == "PROCESSING_FAILED":
            raise VideoProcessingFailedError(video_urn, reason)
        if clock() - start >= max_wait:
            raise VideoProcessingTimeoutError(video_urn, max_wait)
        sleep(interval)
