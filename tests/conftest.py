"""Fixtures compartilhadas: imagens mínimas que `media/inspect.py` reconhece."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Nenhum teste toca o ~/.config/linkedin-bot real (credenciais, jobs, trava)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def make_png(tmp_path: Path):
    """Grava um PNG só com assinatura e IHDR; `extra` muda os bytes sem mudar as dimensões."""

    def make(name: str = "img.png", width: int = 1200, height: int = 800, extra: bytes = b"") -> Path:
        path = tmp_path / name
        ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(ihdr))
            + b"IHDR"
            + ihdr
            + extra
        )
        return path

    return make


def _box(kind: bytes, body: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def mvhd(duration: int, timescale: int = 1000, version: int = 0) -> bytes:
    """Corpo de um `mvhd` com só os campos que importam; o resto é zero."""
    if version == 1:
        times = struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        times = struct.pack(">IIII", 0, 0, timescale, duration)
    return _box(b"mvhd", bytes([version, 0, 0, 0]) + times + b"\x00" * 80)


@pytest.fixture
def make_mp4(tmp_path: Path):
    """Grava um MP4 mínimo: `ftyp`, `moov/mvhd` e um `mdat` de enchimento.

    `seconds=None` grava `mvhd` com duração 0 (como um MP4 fragmentado);
    `moov=False` simula um arquivo truncado; `moov_last` põe o `moov` depois do `mdat`.
    """

    def make(
        name: str = "video.mp4",
        seconds: float | None = 12,
        *,
        size: int = 80 * 1024,
        moov: bool = True,
        moov_last: bool = False,
        version: int = 0,
        extra: bytes = b"",
    ) -> Path:
        path = tmp_path / name
        ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
        timescale = 1000
        duration = 0 if seconds is None else int(seconds * timescale)
        moov_box = _box(b"moov", mvhd(duration, timescale, version)) if moov else b""
        head = len(ftyp) + len(moov_box) + 8 + len(extra)
        mdat = _box(b"mdat", extra + b"\x00" * max(0, size - head))
        parts = [ftyp, mdat, moov_box] if moov_last else [ftyp, moov_box, mdat]
        path.write_bytes(b"".join(parts))
        return path

    return make


@pytest.fixture
def make_gif(tmp_path: Path):
    """Grava um GIF só com o logical screen descriptor."""

    def make(name: str = "img.gif", width: int = 640, height: int = 480, extra: bytes = b"") -> Path:
        path = tmp_path / name
        path.write_bytes(b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + extra)
        return path

    return make
