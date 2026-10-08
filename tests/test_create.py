"""posts/create.py: payload com vídeo e criação que espera o processamento."""

from __future__ import annotations

import httpx
import pytest
from fake_linkedin import VIDEO_URN, Clock, FakeLinkedIn

from linkedin_bot.posts.create import build_payload, create_post_when_ready
from linkedin_bot.transport import retry
from linkedin_bot.transport.errors import InvalidPostError, PostCreationError

PERSON = "urn:li:person:abc"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)


def test_payload_video_uses_media_with_title():
    payload = build_payload(PERSON, "oi", video=(VIDEO_URN, "Demo"))
    assert payload["content"] == {"media": {"id": VIDEO_URN, "title": "Demo"}}


def test_payload_rejects_images_and_video():
    with pytest.raises(InvalidPostError):
        build_payload(PERSON, "oi", images=[("urn:li:image:1", None)], video=(VIDEO_URN, "x"))


def processing(status=400, msg="Media asset is still PROCESSING"):
    return httpx.Response(status, json={"status": status, "message": msg})


def test_waits_while_post_refused_for_processing():
    li, clock = FakeLinkedIn(), Clock()
    li.queue("post", processing(), processing(422, "MEDIA_ASSET_WAITING_UPLOAD"))
    notes, hooks = [], []

    urn = create_post_when_ready(
        li, {"x": 1}, notify=notes.append, clock=clock, sleep=clock.sleep,
        on_attempt=lambda: hooks.append("post"), on_wait=lambda: hooks.append("wait"),
    )

    assert urn == "urn:li:share:1"
    assert clock.sleeps == [5.0, 5.0]
    assert len(notes) == 2
    assert hooks == ["post", "wait", "post", "wait", "post"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"message": "commentary too long"}),
        httpx.Response(400, json={"message": "MEDIA_ASSET_PROCESSING_FAILED"}),
    ],
)
def test_other_refusals_fail_at_once(response):
    li, clock = FakeLinkedIn(), Clock()
    li.queue("post", response)
    with pytest.raises(PostCreationError):
        create_post_when_ready(li, {}, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == []


def test_gives_up_after_max_wait():
    li, clock = FakeLinkedIn(), Clock()
    li.queue("post", *[processing() for _ in range(10)])
    with pytest.raises(PostCreationError, match="PROCESSING"):
        create_post_when_ready(li, {}, max_wait=12, clock=clock, sleep=clock.sleep)
    assert clock.t >= 12


def test_post_creation_is_never_replayed():
    # Uma primeira tentativa pode ter criado o post: reenviar arrisca duplicar.
    li, clock = FakeLinkedIn(), Clock()
    li.queue("post", httpx.Response(503, text="indisponível"))
    with pytest.raises(PostCreationError) as exc:
        create_post_when_ready(li, {}, clock=clock, sleep=clock.sleep)
    assert exc.value.status == 503
    assert [r.url.path for r in li.requests] == ["/rest/posts"]


def test_unprocessable_is_not_mistaken_for_processing():
    li, clock = FakeLinkedIn(), Clock()
    li.queue("post", httpx.Response(422, json={"message": "Unprocessable Entity"}))
    with pytest.raises(PostCreationError):
        create_post_when_ready(li, {}, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == []
