"""Montagem e criação de posts."""

from __future__ import annotations

import re
import time
from typing import Callable

from .. import config
from ..auth.session import LinkedInAuth
from ..media.images import MAX_IMAGES_PER_POST
from ..transport.errors import (
    IMAGES_OR_VIDEO,
    InvalidPostError,
    PostCreationError,
    TooManyImagesError,
)
from ..transport.retry import NO_RETRY
from .little import escape

VISIBILITY = {"public": "PUBLIC", "connections": "CONNECTIONS"}

# Recusa do POST /posts porque o vídeo ainda não está pronto. PROCESSING_FAILED fica de
# fora: é definitiva.
_STILL_PROCESSING = re.compile(
    r"(?<![a-z_])processing(?!_failed)|waiting_upload|not (yet )?available", re.IGNORECASE
)


def build_payload(
    author: str,
    text: str,
    *,
    visibility: str = "PUBLIC",
    images: list[tuple[str, str | None]] | None = None,
    video: tuple[str, str] | None = None,
    disable_reshare: bool = False,
    escape_text: bool = True,
) -> dict:
    """Monta o corpo do POST /rest/posts.

    Uma imagem vira `content.media`; de duas a vinte, `content.multiImage`. Um vídeo
    `(urn, título)` vira `content.media` com `title`.
    """
    images = images or []
    if images and video:
        raise InvalidPostError(IMAGES_OR_VIDEO)
    if len(images) > MAX_IMAGES_PER_POST:
        raise TooManyImagesError(len(images), MAX_IMAGES_PER_POST)

    payload: dict = {
        "author": author,
        "commentary": escape(text) if escape_text else text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": disable_reshare,
    }

    def media(urn: str, alt: str | None) -> dict:
        item: dict = {"id": urn}
        if alt:
            item["altText"] = alt
        return item

    if video:
        payload["content"] = {"media": {"id": video[0], "title": video[1]}}
    elif len(images) == 1:
        payload["content"] = {"media": media(*images[0])}
    elif len(images) >= 2:
        payload["content"] = {
            "multiImage": {"images": [media(u, a) for u, a in images]}
        }

    return payload


def create_post(auth: LinkedInAuth, payload: dict) -> str:
    """Publica e devolve o URN do post (`urn:li:share:…` ou `urn:li:ugcPost:…`)."""
    with auth.api_client() as client:
        r = client.post("/posts", json=payload, extensions={NO_RETRY: True})
        if r.status_code != 201:
            raise PostCreationError(r.status_code, r.text)
        urn = r.headers.get("x-restli-id")
        if not urn:
            raise PostCreationError(r.status_code, "resposta sem o header x-restli-id")
        return urn


def create_post_when_ready(
    auth: LinkedInAuth,
    payload: dict,
    *,
    notify: Callable[[str], None] = lambda _: None,
    on_attempt: Callable[[], None] = lambda: None,
    on_wait: Callable[[], None] = lambda: None,
    max_wait: float = config.VIDEO_PROCESSING_MAX,
    interval: float = config.VIDEO_STATUS_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """`create_post` que insiste enquanto a LinkedIn recusar por vídeo ainda processando.

    Usado quando o token não consegue ler o status do vídeo: a própria criação do post
    vira o teste de "pronto". Cada recusa 4xx é definitiva — nada foi criado —, então
    `on_attempt` marca o início de cada POST e `on_wait` a volta para a espera.
    """
    start = clock()
    while True:
        on_attempt()
        try:
            return create_post(auth, payload)
        except PostCreationError as exc:
            waiting = 400 <= exc.status < 500 and _STILL_PROCESSING.search(exc.body)
            if not waiting or clock() - start >= max_wait:
                raise
        on_wait()
        notify("  (vídeo ainda processando na LinkedIn — tentando de novo)")
        sleep(interval)


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"
