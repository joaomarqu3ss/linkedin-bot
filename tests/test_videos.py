"""media/videos.py: validação local, upload multipart, finalize e espera do processamento."""

from __future__ import annotations

import os

import httpx
import pytest

from linkedin_bot.media import videos
from linkedin_bot.media.videos import inspect_video, upload_video, wait_processed
from linkedin_bot.transport import retry
from linkedin_bot.transport.errors import (
    CorruptVideoError,
    UnsupportedVideoFormatError,
    UploadFailedError,
    VideoDurationError,
    VideoNotFoundError,
    VideoProcessingFailedError,
    VideoProcessingTimeoutError,
    VideoSizeError,
)

from fake_linkedin import VIDEO_URN, Clock, FakeLinkedIn


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)


# --- inspect_video ----------------------------------------------------------


def test_inspect_ok(make_mp4):
    info = inspect_video(make_mp4(seconds=75))
    assert info.duration == pytest.approx(75)
    assert info.size_bytes == 80 * 1024
    assert info.describe() == "video.mp4 (1:15, 0.1 MB)"


def test_inspect_unknown_duration_passes(make_mp4):
    info = inspect_video(make_mp4(seconds=None))
    assert info.duration is None
    assert info.describe() == "video.mp4 (duração ?, 0.1 MB)"


def test_inspect_missing(tmp_path):
    with pytest.raises(VideoNotFoundError):
        inspect_video(tmp_path / "nao.mp4")


@pytest.mark.parametrize("name", ["clip.mov", "clip.webm", "clip"])
def test_inspect_rejects_other_formats(make_mp4, name):
    with pytest.raises(UnsupportedVideoFormatError, match=r"\(aceito: \.mp4\)"):
        inspect_video(make_mp4(name))


def test_inspect_rejects_mp4_without_signature(tmp_path):
    path = tmp_path / "falso.mp4"
    path.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100_000)  # cabeçalho de WebM
    with pytest.raises(UnsupportedVideoFormatError):
        inspect_video(path)


def test_inspect_rejects_truncated(make_mp4):
    with pytest.raises(CorruptVideoError, match="incompleto ou corrompido"):
        inspect_video(make_mp4(moov=False))


def test_inspect_rejects_too_small(make_mp4):
    with pytest.raises(VideoSizeError):
        inspect_video(make_mp4(size=10 * 1024))


def test_inspect_rejects_too_large(make_mp4, monkeypatch):
    monkeypatch.setattr(videos, "MAX_BYTES", 79 * 1024)
    with pytest.raises(VideoSizeError):
        inspect_video(make_mp4())


@pytest.mark.parametrize("seconds", [2.9, 30 * 60 + 1])
def test_inspect_rejects_duration(make_mp4, seconds):
    with pytest.raises(VideoDurationError):
        inspect_video(make_mp4(seconds=seconds))


@pytest.mark.parametrize("seconds", [3, 30 * 60])
def test_inspect_accepts_duration_bounds(make_mp4, seconds):
    assert inspect_video(make_mp4(seconds=seconds)).duration == pytest.approx(seconds)


# --- upload -----------------------------------------------------------------


def test_upload_in_parts_and_finalize(make_mp4):
    path = make_mp4(size=100 * 1024)
    li = FakeLinkedIn(part_size=32 * 1024)
    seen = []

    urn = upload_video(li, inspect_video(path), on_bytes=lambda d, t: seen.append((d, t)))

    assert urn == VIDEO_URN
    assert li.initialized == {
        "initializeUploadRequest": {
            "owner": "urn:li:person:abc",
            "fileSizeBytes": 100 * 1024,
            "uploadCaptions": False,
            "uploadThumbnail": False,
        }
    }
    assert b"".join(li.parts[i] for i in sorted(li.parts)) == path.read_bytes()
    assert li.finalized == {
        "finalizeUploadRequest": {
            "video": VIDEO_URN,
            "uploadToken": "tok",
            "uploadedPartIds": ["etag-0", "etag-1", "etag-2", "etag-3"],
        }
    }
    total = 100 * 1024
    assert seen == [(32768, total), (65536, total), (98304, total), (total, total)]


def test_upload_retries_a_failed_part(make_mp4):
    path = make_mp4(size=100 * 1024)
    li = FakeLinkedIn()
    li.queue("part", httpx.Response(503))

    upload_video(li, inspect_video(path))

    assert b"".join(li.parts[i] for i in sorted(li.parts)) == path.read_bytes()
    puts = [r for r in li.requests if r.method == "PUT"]
    assert len(puts) == 5  # 4 partes + 1 retentativa


def test_upload_part_without_etag_fails(make_mp4):
    li = FakeLinkedIn()
    li.queue("part", httpx.Response(200))
    with pytest.raises(UploadFailedError, match="ETag"):
        upload_video(li, inspect_video(make_mp4()))
    assert li.finalized is None


def test_upload_part_rejected_fails_before_finalize(make_mp4):
    li = FakeLinkedIn()
    li.queue("part", httpx.Response(400, text="bad part"))
    with pytest.raises(UploadFailedError, match="HTTP 400"):
        upload_video(li, inspect_video(make_mp4()))
    assert li.finalized is None


def test_initialize_rejected(make_mp4):
    li = FakeLinkedIn()
    li.queue("initialize", httpx.Response(422, text="nope"))
    with pytest.raises(UploadFailedError, match="initializeUpload"):
        upload_video(li, inspect_video(make_mp4()))


def test_finalize_rejected(make_mp4):
    li = FakeLinkedIn()
    li.queue("finalize", httpx.Response(400, text="partes faltando"))
    with pytest.raises(UploadFailedError, match="finalizeUpload"):
        upload_video(li, inspect_video(make_mp4()))


# --- wait_processed ---------------------------------------------------------


def test_wait_until_available():
    li, clock = FakeLinkedIn(), Clock()
    li.statuses.extend(["WAITING_UPLOAD", "PROCESSING", "AVAILABLE"])
    assert wait_processed(li, VIDEO_URN, clock=clock, sleep=clock.sleep) == "AVAILABLE"
    assert clock.sleeps == [5.0, 5.0]


def test_wait_status_unreadable_returns_none():
    li, clock = FakeLinkedIn(), Clock()
    li.statuses.append(httpx.Response(403, json={"status": 403}))
    assert wait_processed(li, VIDEO_URN, clock=clock, sleep=clock.sleep) is None
    assert clock.sleeps == []


def test_wait_processing_failed():
    li, clock = FakeLinkedIn(), Clock()
    li.statuses.extend(["PROCESSING", "PROCESSING_FAILED"])
    with pytest.raises(VideoProcessingFailedError, match="codec não suportado") as exc:
        wait_processed(li, VIDEO_URN, clock=clock, sleep=clock.sleep)
    assert VIDEO_URN in str(exc.value)


def test_wait_times_out():
    li, clock = FakeLinkedIn(), Clock()
    li.statuses.append("PROCESSING")
    with pytest.raises(VideoProcessingTimeoutError) as exc:
        wait_processed(li, VIDEO_URN, max_wait=20, clock=clock, sleep=clock.sleep)
    assert VIDEO_URN in str(exc.value)
    assert clock.t >= 20


def test_wait_keeps_polling_through_transient_errors():
    li, clock = FakeLinkedIn(), Clock()
    # 3 respostas 503 esgotam o retry do transporte; a espera segue em vez de desistir
    li.statuses.extend([httpx.Response(503)] * 3 + ["AVAILABLE"])
    assert wait_processed(li, VIDEO_URN, clock=clock, sleep=clock.sleep) == "AVAILABLE"
    assert clock.sleeps == [5.0]


def test_failed_finalize_names_the_orphan(make_mp4):
    li = FakeLinkedIn()
    li.queue("finalize", httpx.Response(400, text="partes faltando"))
    with pytest.raises(UploadFailedError) as exc:
        upload_video(li, inspect_video(make_mp4()))
    assert VIDEO_URN in " ".join(exc.value.__notes__)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lê tudo")
def test_unreadable_file_is_a_typed_error(make_mp4):
    path = make_mp4()
    path.chmod(0)
    try:
        with pytest.raises(CorruptVideoError, match="não foi possível ler"):
            inspect_video(path)
    finally:
        path.chmod(0o644)
