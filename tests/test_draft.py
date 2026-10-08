from __future__ import annotations

from types import SimpleNamespace

import pytest

from linkedin_bot.posts import draft as draft_mod
from linkedin_bot.posts.create import build_payload
from linkedin_bot.posts.draft import prepare, publish
from linkedin_bot.transport.errors import (
    DraftChangedError,
    ImageNotFoundError,
    InvalidPostError,
    TooManyImagesError,
    UnsupportedImageFormatError,
)

PERSON = "urn:li:person:abc123"


# --- prepare ----------------------------------------------------------------


def test_prepare_strips_text_and_maps_visibility():
    d = prepare("  olá  ", visibility="connections")
    assert d.text == "olá"
    assert d.visibility == "CONNECTIONS"
    assert d.images == ()


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_prepare_rejects_empty_text(text):
    with pytest.raises(InvalidPostError, match="o texto do post está vazio"):
        prepare(text)


def test_prepare_rejects_invalid_visibility():
    with pytest.raises(InvalidPostError, match="visibilidade"):
        prepare("oi", visibility="PUBLIC")


def test_prepare_rejects_21_images_before_opening_files(tmp_path):
    # Os arquivos não existem: se algum fosse aberto, o erro seria ImageNotFoundError.
    images = [(tmp_path / f"{i}.png", None) for i in range(21)]
    with pytest.raises(TooManyImagesError):
        prepare("oi", images)


def test_prepare_rejects_unsupported_format(tmp_path):
    path = tmp_path / "foto.webp"
    path.write_bytes(b"RIFF0000WEBP")
    with pytest.raises(UnsupportedImageFormatError):
        prepare("oi", [(path, None)])


def test_prepare_rejects_missing_image(tmp_path):
    with pytest.raises(ImageNotFoundError):
        prepare("oi", [(tmp_path / "nao-existe.png", None)])


def test_prepare_inspects_and_hashes_images(make_png):
    path = make_png()
    (img,) = prepare("oi", [(str(path), "alt")]).images
    assert (img.info.width, img.info.height) == (1200, 800)
    assert img.alt == "alt"
    assert len(img.sha256) == 64


# --- preview_payload --------------------------------------------------------


def _placeholder_payload(text, alts, visibility="PUBLIC"):
    return build_payload(
        "urn:li:person:<AUTOR>",
        text,
        visibility=visibility,
        images=[(f"urn:li:image:<UPLOAD_{i + 1}>", a) for i, a in enumerate(alts)],
    )


def test_preview_payload_text_only():
    payload = prepare("teste").preview_payload()
    assert payload == _placeholder_payload("teste", [])
    assert "content" not in payload


def test_preview_payload_one_image(make_png):
    payload = prepare("teste", [(make_png(), "uma")]).preview_payload()
    assert payload == _placeholder_payload("teste", ["uma"])
    assert payload["content"] == {
        "media": {"id": "urn:li:image:<UPLOAD_1>", "altText": "uma"}
    }


def test_preview_payload_two_images(make_png, make_gif):
    d = prepare("teste", [(make_png(), "a"), (make_gif(), None)], visibility="connections")
    payload = d.preview_payload()
    assert payload == _placeholder_payload("teste", ["a", None], "CONNECTIONS")
    assert payload["content"] == {
        "multiImage": {
            "images": [
                {"id": "urn:li:image:<UPLOAD_1>", "altText": "a"},
                {"id": "urn:li:image:<UPLOAD_2>"},
            ]
        }
    }


# --- fingerprint ------------------------------------------------------------


def test_fingerprint_is_stable_for_equal_inputs(make_png, make_gif, tmp_path):
    a, b = make_png(), make_gif()
    first = prepare("texto", [(a, "x"), (b, "y")]).fingerprint
    second = prepare("  texto ", [(a, "x"), (b, "y")]).fingerprint
    assert first == second


def test_fingerprint_ignores_path(make_png):
    a = make_png("a.png")
    b = make_png("copia.png")
    assert prepare("t", [(a, None)]).fingerprint == prepare("t", [(b, None)]).fingerprint


def test_fingerprint_changes_with_each_field(make_png, make_gif):
    a, b = make_png(), make_gif()
    a_mod = make_png("a2.png", extra=b"x")
    base = prepare("texto", [(a, "x"), (b, "y")]).fingerprint
    variants = {
        "texto": prepare("outro", [(a, "x"), (b, "y")]),
        "alt": prepare("texto", [(a, "z"), (b, "y")]),
        "visibilidade": prepare("texto", [(a, "x"), (b, "y")], visibility="connections"),
        "bytes": prepare("texto", [(a_mod, "x"), (b, "y")]),
        "ordem": prepare("texto", [(b, "y"), (a, "x")]),
    }
    for field, d in variants.items():
        assert d.fingerprint != base, field


# --- publish ----------------------------------------------------------------


@pytest.fixture
def fake_api(monkeypatch):
    calls = SimpleNamespace(uploads=[], posts=[])

    def fake_upload(auth, info, *, notify=lambda _: None):
        calls.uploads.append(info.path.name)
        return f"urn:li:image:{len(calls.uploads)}"

    def fake_create(auth, payload):
        calls.posts.append(payload)
        return "urn:li:share:999"

    monkeypatch.setattr(draft_mod, "upload_image", fake_upload)
    monkeypatch.setattr(draft_mod, "create_post", fake_create)
    return calls


AUTH = SimpleNamespace(person_urn=PERSON, name="Fulano")


def test_publish_uploads_in_order_and_posts_real_payload(fake_api, make_png, make_gif):
    d = prepare("olá", [(make_png("1.png"), "a"), (make_gif("2.gif"), "b")])
    progress, messages = [], []

    urn = publish(AUTH, d, notify=messages.append, on_progress=lambda *a: progress.append(a))

    assert urn == "urn:li:share:999"
    assert fake_api.uploads == ["1.png", "2.gif"]
    assert fake_api.posts == [
        build_payload(
            PERSON,
            "olá",
            visibility="PUBLIC",
            images=[("urn:li:image:1", "a"), ("urn:li:image:2", "b")],
        )
    ]
    assert [p[:2] for p in progress] == [(1, 3), (2, 3), (3, 3)]
    assert progress[0][2] == "imagem 1/2 enviada"
    assert progress[-1][2] == "post publicado"
    assert "\n→ publicando..." in messages


def test_publish_text_only(fake_api):
    progress = []
    publish(AUTH, prepare("só texto"), on_progress=lambda *a: progress.append(a))
    assert fake_api.uploads == []
    assert progress == [(1, 1, "post publicado")]


def test_publish_rejects_changed_image(fake_api, make_png, make_gif):
    a, b = make_png("1.png"), make_gif("2.gif")
    d = prepare("olá", [(a, None), (b, None)])
    b.write_bytes(b.read_bytes() + b"mudou")

    with pytest.raises(DraftChangedError, match="mudou depois do preview"):
        publish(AUTH, d)
    assert fake_api.uploads == []
    assert fake_api.posts == []


def test_publish_rejects_missing_image(fake_api, make_png):
    path = make_png()
    d = prepare("olá", [(path, None)])
    path.unlink()

    with pytest.raises(DraftChangedError):
        publish(AUTH, d)
    assert fake_api.uploads == []
    assert fake_api.posts == []
