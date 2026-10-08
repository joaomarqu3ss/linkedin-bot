from __future__ import annotations

from types import SimpleNamespace

import pytest

from linkedin_bot.posts import draft as draft_mod
from linkedin_bot.posts.create import build_payload
from linkedin_bot.media.videos import UNKNOWN_DURATION_WARNING
from linkedin_bot.posts.draft import (
    PROCESSING,
    PUBLISHED,
    PUBLISHING,
    UPLOADING,
    Event,
    Images,
    Video,
    prepare,
    publish,
)
from linkedin_bot.transport.errors import (
    DraftChangedError,
    ImageNotFoundError,
    InvalidPostError,
    PostCreationError,
    TooManyImagesError,
    UnsupportedImageFormatError,
    UnsupportedVideoFormatError,
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
    events, messages = [], []

    urn = publish(AUTH, d, notify=messages.append, on_event=events.append)

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
    assert events == [
        Event(UPLOADING, 1, 2, "imagens"),
        Event(UPLOADING, 2, 2, "imagens"),
        Event(PUBLISHING),
        Event(PUBLISHED, urn="urn:li:share:999"),
    ]
    assert "\n→ publicando..." in messages


def test_publish_text_only(fake_api):
    events = []
    publish(AUTH, prepare("só texto"), on_event=events.append)
    assert fake_api.uploads == []
    assert events == [Event(PUBLISHING), Event(PUBLISHED, urn="urn:li:share:999")]


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


# --- vídeo --------------------------------------------------------------------


def test_prepare_without_media_has_no_attachment():
    d = prepare("oi")
    assert d.attachment is None and d.images == () and d.video is None


def test_prepare_images_attachment(make_png):
    d = prepare("oi", [(make_png(), None)])
    assert isinstance(d.attachment, Images) and d.video is None


def test_prepare_video_defaults_title_to_file_stem(make_mp4):
    d = prepare("oi", video=make_mp4("Minha demo.mp4"))
    assert isinstance(d.attachment, Video)
    assert d.video.title == "Minha demo"
    assert d.video.info.duration == pytest.approx(12)
    assert len(d.video.sha256) == 64
    assert d.images == () and d.warnings == []


def test_prepare_video_title_is_stripped_and_blank_falls_back(make_mp4):
    path = make_mp4()
    assert prepare("oi", video=path, video_title="  Lançamento ").video.title == "Lançamento"
    assert prepare("oi", video=path, video_title="   ").video.title == "video"


def test_prepare_rejects_images_and_video(make_png, make_mp4):
    with pytest.raises(InvalidPostError, match="imagens ou um vídeo"):
        prepare("oi", [(make_png(), None)], video=make_mp4())


def test_prepare_rejects_non_mp4(make_mp4):
    with pytest.raises(UnsupportedVideoFormatError):
        prepare("oi", video=make_mp4("clip.mov"))


def test_prepare_video_unknown_duration_warns(make_mp4):
    d = prepare("oi", video=make_mp4(seconds=None))
    assert d.warnings == [UNKNOWN_DURATION_WARNING]


def test_preview_payload_video(make_mp4):
    payload = prepare("teste", video=make_mp4(), video_title="T").preview_payload()
    assert payload["content"] == {"media": {"id": "urn:li:video:<UPLOAD>", "title": "T"}}


def test_fingerprint_video(make_mp4):
    a = make_mp4("a.mp4")
    base = prepare("t", video=a).fingerprint
    assert prepare("t", video=make_mp4("b.mp4"), video_title="a").fingerprint == base
    assert prepare("t", video=a, video_title="outro").fingerprint != base
    assert prepare("t", video=make_mp4("c.mp4", extra=b"x"), video_title="a").fingerprint != base
    assert prepare("t").fingerprint != base


@pytest.fixture
def fake_video_api(monkeypatch):
    calls = SimpleNamespace(uploads=[], waits=[], posts=[], ready_posts=[], status="AVAILABLE")

    def fake_upload_video(auth, info, *, notify=lambda _: None, on_bytes=lambda *_: None):
        calls.uploads.append(info.path.name)
        on_bytes(info.size_bytes // 2, info.size_bytes)
        on_bytes(info.size_bytes, info.size_bytes)
        return "urn:li:video:V1"

    def fake_wait(auth, urn, *, notify=lambda _: None):
        calls.waits.append(urn)
        if isinstance(calls.status, Exception):
            raise calls.status
        return calls.status

    def fake_create(auth, payload):
        calls.posts.append(payload)
        return "urn:li:share:7"

    def fake_create_ready(auth, payload, *, notify, on_attempt, on_wait):
        calls.ready_posts.append(payload)
        on_attempt()
        on_wait()
        on_attempt()
        return "urn:li:share:8"

    monkeypatch.setattr(draft_mod, "upload_video", fake_upload_video)
    monkeypatch.setattr(draft_mod, "wait_processed", fake_wait)
    monkeypatch.setattr(draft_mod, "create_post", fake_create)
    monkeypatch.setattr(draft_mod, "create_post_when_ready", fake_create_ready)
    return calls


def test_publish_video(fake_video_api, make_mp4):
    d = prepare("olá", video=make_mp4(), video_title="Demo")
    events = []

    urn = publish(AUTH, d, on_event=events.append)

    size = d.video.info.size_bytes
    assert urn == "urn:li:share:7"
    assert fake_video_api.waits == ["urn:li:video:V1"]
    assert fake_video_api.posts == [
        build_payload(PERSON, "olá", video=("urn:li:video:V1", "Demo"))
    ]
    assert events == [
        Event(UPLOADING, 0, size, "bytes"),
        Event(UPLOADING, size // 2, size, "bytes"),
        Event(UPLOADING, size, size, "bytes"),
        Event(PROCESSING),
        Event(PUBLISHING),
        Event(PUBLISHED, urn="urn:li:share:7"),
    ]


def test_publish_video_status_unreadable_waits_on_post_creation(fake_video_api, make_mp4):
    fake_video_api.status = None
    events = []
    urn = publish(AUTH, prepare("olá", video=make_mp4()), on_event=events.append)
    assert urn == "urn:li:share:8"
    assert fake_video_api.posts == [] and len(fake_video_api.ready_posts) == 1
    # PUBLISHING só durante cada POST; entre tentativas a fase volta a PROCESSING
    assert [e.phase for e in events if e.phase != UPLOADING] == [
        PROCESSING, PUBLISHING, PROCESSING, PUBLISHING, PUBLISHED
    ]


def test_publish_video_ambiguous_post_failure_does_not_claim_orphan(fake_video_api, make_mp4, monkeypatch):
    def flaky(auth, payload):
        raise PostCreationError(503, "indisponível")

    monkeypatch.setattr(draft_mod, "create_post", flaky)
    with pytest.raises(PostCreationError) as exc:
        publish(AUTH, prepare("olá", video=make_mp4()))
    assert not getattr(exc.value, "__notes__", None)


def test_publish_video_failure_after_upload_names_the_orphan(fake_video_api, make_mp4):
    fake_video_api.status = PostCreationError(400, "nope")
    messages = []
    with pytest.raises(PostCreationError) as exc:
        publish(AUTH, prepare("olá", video=make_mp4()), notify=messages.append)
    assert "urn:li:video:V1" in " ".join(exc.value.__notes__)
    assert any("urn:li:video:V1" in m for m in messages)


def test_publish_video_rejects_changed_file(fake_video_api, make_mp4):
    path = make_mp4()
    d = prepare("olá", video=path)
    path.write_bytes(path.read_bytes() + b"mudou")
    with pytest.raises(DraftChangedError):
        publish(AUTH, d)
    assert fake_video_api.uploads == []
