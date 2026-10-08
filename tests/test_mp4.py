"""Leitura do MP4 direto das boxes: assinatura, presença do `moov` e duração do `mvhd`."""

from __future__ import annotations

import struct

import pytest

from linkedin_bot.media.mp4 import probe


@pytest.mark.parametrize("version", [0, 1])
def test_duration_from_mvhd(make_mp4, version):
    p = probe(make_mp4(seconds=12.5, version=version))
    assert p is not None and p.has_moov
    assert p.duration == pytest.approx(12.5)


def test_moov_after_mdat(make_mp4):
    p = probe(make_mp4(seconds=90, moov_last=True))
    assert p is not None and p.has_moov and p.duration == pytest.approx(90)


def test_zero_duration_is_unknown(make_mp4):
    p = probe(make_mp4(seconds=None))
    assert p is not None and p.has_moov and p.duration is None


def test_missing_moov(make_mp4):
    p = probe(make_mp4(moov=False))
    assert p is not None and not p.has_moov and p.duration is None


def test_truncated_moov_counts_as_missing(make_mp4):
    path = make_mp4(moov_last=True)
    data = path.read_bytes()
    path.write_bytes(data[:-20])  # o moov declara mais bytes do que o arquivo tem
    p = probe(path)
    assert p is not None and not p.has_moov


def test_not_an_mp4(tmp_path):
    path = tmp_path / "x.mp4"
    path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert probe(path) is None


def test_empty_file(tmp_path):
    path = tmp_path / "x.mp4"
    path.write_bytes(b"")
    assert probe(path) is None


def test_largesize_box(tmp_path):
    # size == 1: o tamanho real vem nos 8 bytes seguintes ao tipo
    ftyp = struct.pack(">I", 16) + b"ftypisom\x00\x00\x02\x00"
    body = b"\x00" * 32
    mdat = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + len(body)) + body
    mvhd = (
        struct.pack(">I", 8 + 4 + 16 + 80) + b"mvhd" + b"\x00" * 4
        + struct.pack(">IIII", 0, 0, 600, 600 * 7) + b"\x00" * 80
    )
    moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
    path = tmp_path / "big.mp4"
    path.write_bytes(ftyp + mdat + moov)
    p = probe(path)
    assert p is not None and p.duration == pytest.approx(7)
