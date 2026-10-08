from __future__ import annotations

import io
import json
import os
import signal
from types import SimpleNamespace

import pytest

from linkedin_bot.auth.session import LinkedInAuth
from linkedin_bot.cli.__main__ import main
from linkedin_bot.cli.post_cmd import UploadProgress
from linkedin_bot.media.videos import UNKNOWN_DURATION_WARNING
from linkedin_bot.posts import draft as draft_mod
from linkedin_bot.posts.draft import PROCESSING, PUBLISHING, UPLOADING, Event, prepare


def test_dry_run_prints_preview_payload(capsys, make_png, make_gif):
    a, b = make_png("a.png"), make_gif("b.gif")

    rc = main(["post", "teste", "--image", str(a), "--image", str(b), "--alt", "d", "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "  imagem ok: a.png (1200x800, 0 KB)\n" in out
    assert "  imagem ok: b.gif (640x480, 0 KB)\n" in out
    head, _, body = out.partition("--- dry-run: nada foi enviado nem publicado ---\n")
    assert json.loads(body) == prepare("teste", [(a, "d"), (b, "d")]).preview_payload()


def test_empty_text_exits_1(capsys):
    rc = main(["post", "  ", "--dry-run"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == "erro: o texto do post está vazio\n"


def test_publish_prints_same_lines(capsys, monkeypatch, make_png):
    auth = SimpleNamespace(name="Fulano", person_urn="urn:li:person:abc")
    calls = SimpleNamespace(uploads=[], posts=[])

    def fake_upload(auth, info, *, notify=lambda _: None):
        calls.uploads.append(info.path.name)
        return "urn:li:image:1"

    def fake_create(auth, payload):
        calls.posts.append(payload)
        return "urn:li:share:42"

    monkeypatch.setattr(LinkedInAuth, "load", classmethod(lambda cls, store=None: auth))
    monkeypatch.setattr(draft_mod, "upload_image", fake_upload)
    monkeypatch.setattr(draft_mod, "create_post", fake_create)

    rc = main(["post", "olá", "--image", str(make_png("a.png"))])

    assert rc == 0
    assert calls.uploads == ["a.png"]
    assert len(calls.posts) == 1
    assert capsys.readouterr().out == (
        "  imagem ok: a.png (1200x800, 0 KB)\n"
        "\n"
        "Autor: Fulano (urn:li:person:abc)\n"
        "\n"
        "→ publicando...\n"
        "\n"
        "✓ Post publicado (PUBLIC)\n"
        "  urn:li:share:42\n"
        "  https://www.linkedin.com/feed/update/urn:li:share:42/\n"
    )


# --- vídeo ----------------------------------------------------------------------

def test_dry_run_video(capsys, make_mp4):
    path = make_mp4("demo.mp4", seconds=75)

    rc = main(["post", "teste", "--video", str(path), "--title", "Demo", "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("  vídeo ok: demo.mp4 (1:15, 0.1 MB) — título: Demo\n")
    _, _, body = out.partition("--- dry-run: nada foi enviado nem publicado ---\n")
    assert json.loads(body) == prepare("teste", video=path, video_title="Demo").preview_payload()


def test_dry_run_video_warns_unknown_duration(capsys, make_mp4):
    rc = main(["post", "teste", "--video", str(make_mp4(seconds=None)), "--dry-run"])
    assert rc == 0
    assert f"  ⚠ {UNKNOWN_DURATION_WARNING}\n" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--image", "a.png"], "not allowed with argument"),
        (["--alt", "x"], "--alt vale só para --image"),
    ],
)
def test_video_conflicts(capsys, make_mp4, extra, message):
    argv = ["post", "t", "--video", str(make_mp4()), *extra, "--dry-run"]
    try:
        rc = main(argv)
    except SystemExit as exc:  # erro do argparse
        rc = exc.code
    assert rc in (1, 2)
    assert message in capsys.readouterr().err


def test_title_requires_video(capsys, make_png):
    rc = main(["post", "t", "--image", str(make_png()), "--title", "x", "--dry-run"])
    assert rc == 1
    assert "--title vale só para --video" in capsys.readouterr().err


def test_mov_is_refused(capsys, make_mp4):
    rc = main(["post", "t", "--video", str(make_mp4("clip.mov")), "--dry-run"])
    assert rc == 1
    assert "(aceito: .mp4)" in capsys.readouterr().err


@pytest.fixture
def video_api(monkeypatch):
    auth = SimpleNamespace(name="Fulano", person_urn="urn:li:person:abc")
    calls = SimpleNamespace(posts=[], during_post=None, upload_error=None)

    def fake_upload_video(auth, info, *, notify=lambda _: None, on_bytes=lambda *_: None):
        if calls.upload_error:
            raise calls.upload_error
        for i in range(1, 21):
            on_bytes(info.size_bytes * i // 20, info.size_bytes)
        notify("  ✓ urn:li:video:V1 enviado")
        return "urn:li:video:V1"

    def fake_create(auth, payload):
        if calls.during_post:
            calls.during_post()
        calls.posts.append(payload)
        return "urn:li:share:42"

    monkeypatch.setattr(LinkedInAuth, "load", classmethod(lambda cls, store=None: auth))
    monkeypatch.setattr(draft_mod, "upload_video", fake_upload_video)
    monkeypatch.setattr(draft_mod, "wait_processed", lambda *a, **k: "AVAILABLE")
    monkeypatch.setattr(draft_mod, "create_post", fake_create)
    return calls


def test_publish_video_prints_progress_every_10_percent(capsys, make_mp4, video_api):
    rc = main(["post", "olá", "--video", str(make_mp4("demo.mp4"))])

    out = capsys.readouterr().out
    assert rc == 0
    progress = [line for line in out.splitlines() if "enviando" in line]
    assert [line.rsplit("(", 1)[1] for line in progress] == [
        "0%)", "10%)", "20%)", "30%)", "40%)", "50%)", "60%)", "70%)", "80%)", "90%)", "100%)"
    ]
    assert "  ✓ urn:li:video:V1 enviado\n" in out
    assert "✓ Post publicado (PUBLIC)\n  urn:li:share:42\n" in out
    assert video_api.posts[0]["content"] == {"media": {"id": "urn:li:video:V1", "title": "demo"}}


def test_ctrl_c_before_post_aborts(capsys, make_mp4, video_api):
    video_api.upload_error = KeyboardInterrupt()
    rc = main(["post", "olá", "--video", str(make_mp4())])
    assert rc == 130
    assert video_api.posts == []


def test_ctrl_c_while_posting_finishes_the_post(capsys, make_mp4, video_api):
    before = signal.getsignal(signal.SIGINT)
    video_api.during_post = lambda: os.kill(os.getpid(), signal.SIGINT)

    rc = main(["post", "olá", "--video", str(make_mp4())])

    captured = capsys.readouterr()
    assert rc == 0
    assert "urn:li:share:42" in captured.out
    assert "não dá para cancelar agora" in captured.err
    assert signal.getsignal(signal.SIGINT) is before


def test_tty_progress_rewrites_one_line():
    stream = io.StringIO()
    progress = UploadProgress(stream, tty=True)
    mb = 1024 * 1024

    progress(Event(UPLOADING, 0, 500 * mb, "bytes"))
    progress(Event(UPLOADING, 148 * mb, 500 * mb, "bytes"))
    progress(Event(PROCESSING))
    progress(Event(PUBLISHING))

    assert stream.getvalue() == (
        "\r  → enviando 0.0/500.0 MB (0%)"
        "\r  → enviando 148.0/500.0 MB (29%)"
        "\n"
    )


def test_progress_ignores_images():
    stream = io.StringIO()
    progress = UploadProgress(stream, tty=False)
    progress(Event(UPLOADING, 1, 2, "imagens"))
    assert stream.getvalue() == ""
