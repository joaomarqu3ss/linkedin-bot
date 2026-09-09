"""Leitura das dimensões de PNG, JPEG e GIF direto do cabeçalho.

Evita uma dependência de imagem só para checar o limite de pixels. Se o formato
não for reconhecido, devolve None e a validação de pixels é simplesmente pulada —
o servidor ainda rejeitaria, mas aí sem gastar o upload.
"""

from __future__ import annotations

import struct
from pathlib import Path


def dimensions(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as fh:
        head = fh.read(32)

        # PNG: assinatura + IHDR com largura/altura big-endian
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])

        # GIF: logical screen descriptor, little-endian
        if head[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", head[6:10])
            return w, h

        # JPEG: varre os segmentos até um marcador SOF
        if head[:2] == b"\xff\xd8":
            fh.seek(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                code = marker[1]
                (length,) = struct.unpack(">H", fh.read(2))
                # SOF0-SOF15, exceto DHT(C4), JPGA(C8) e DAC(CC)
                if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                    data = fh.read(5)
                    h, w = struct.unpack(">HH", data[1:5])
                    return w, h
                fh.seek(length - 2, 1)

    return None
