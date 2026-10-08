"""Fixtures compartilhadas: imagens mínimas que `media/inspect.py` reconhece."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


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


@pytest.fixture
def make_gif(tmp_path: Path):
    """Grava um GIF só com o logical screen descriptor."""

    def make(name: str = "img.gif", width: int = 640, height: int = 480, extra: bytes = b"") -> Path:
        path = tmp_path / name
        path.write_bytes(b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + extra)
        return path

    return make
