"""Leitura mínima de MP4 (ISO BMFF) direto das boxes.

Mesma ideia de `inspect.py`: nenhuma dependência de mídia, só o suficiente para
recusar barato o que a LinkedIn recusaria depois de um upload inteiro. Um MP4 é uma
sequência de boxes `[tamanho][tipo][corpo]`; a duração mora em `moov/mvhd`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

_UNKNOWN_32 = 0xFFFFFFFF
_UNKNOWN_64 = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class Mp4Probe:
    has_moov: bool
    duration: float | None  # segundos; None quando o mvhd não informa (ex.: MP4 fragmentado)


@dataclass(frozen=True)
class _Box:
    kind: bytes
    body: int  # offset do corpo
    end: int   # offset do fim (exclusivo)


def _boxes(fh: BinaryIO, start: int, end: int) -> Iterator[_Box]:
    """Boxes inteiras em [start, end). Para na primeira malformada ou que passa do fim."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        body = pos + 8
        if size == 1:
            large = fh.read(8)
            if len(large) < 8:
                return
            (size,) = struct.unpack(">Q", large)
            body += 8
        elif size == 0:
            size = end - pos  # vai até o fim do contêiner
        if size < body - pos or pos + size > end:
            return
        yield _Box(kind, body, pos + size)
        pos += size


def _mvhd_duration(fh: BinaryIO, box: _Box) -> float | None:
    fh.seek(box.body)
    head = fh.read(4)
    if len(head) < 4:
        return None
    if head[0] == 1:
        data = fh.read(28)
        if len(data) < 28:
            return None
        _, _, timescale, duration = struct.unpack(">QQIQ", data)
        unknown = duration == _UNKNOWN_64
    else:
        data = fh.read(16)
        if len(data) < 16:
            return None
        _, _, timescale, duration = struct.unpack(">IIII", data)
        unknown = duration == _UNKNOWN_32
    if unknown or not timescale or not duration:
        return None
    return duration / timescale


def probe(path: Path) -> Mp4Probe | None:
    """None se o arquivo não começa com `ftyp` — não é um MP4."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        top = _boxes(fh, 0, size)
        first = next(top, None)
        if first is None or first.kind != b"ftyp":
            return None
        for box in top:
            if box.kind != b"moov":
                continue
            duration = None
            for child in _boxes(fh, box.body, box.end):
                if child.kind == b"mvhd":
                    duration = _mvhd_duration(fh, child)
                    break
            return Mp4Probe(has_moov=True, duration=duration)
    return Mp4Probe(has_moov=False, duration=None)
