from __future__ import annotations

import json
from types import SimpleNamespace

from linkedin_bot.auth.session import LinkedInAuth
from linkedin_bot.cli.__main__ import main
from linkedin_bot.posts import draft as draft_mod
from linkedin_bot.posts.draft import prepare


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
