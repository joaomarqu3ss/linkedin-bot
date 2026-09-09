"""Montagem e criação de posts."""

from __future__ import annotations

from ..auth.session import LinkedInAuth
from ..media.images import MAX_IMAGES_PER_POST
from ..transport.errors import PostCreationError, TooManyImagesError
from .little import escape

VISIBILITY = {"public": "PUBLIC", "connections": "CONNECTIONS"}


def build_payload(
    author: str,
    text: str,
    *,
    visibility: str = "PUBLIC",
    images: list[tuple[str, str | None]] | None = None,
    disable_reshare: bool = False,
    escape_text: bool = True,
) -> dict:
    """Monta o corpo do POST /rest/posts.

    Uma imagem vira `content.media`; de duas a vinte, `content.multiImage`.
    """
    images = images or []
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

    if len(images) == 1:
        payload["content"] = {"media": media(*images[0])}
    elif len(images) >= 2:
        payload["content"] = {
            "multiImage": {"images": [media(u, a) for u, a in images]}
        }

    return payload


def create_post(auth: LinkedInAuth, payload: dict) -> str:
    """Publica e devolve o URN do post (`urn:li:share:…` ou `urn:li:ugcPost:…`)."""
    with auth.api_client() as client:
        r = client.post("/posts", json=payload)
        if r.status_code != 201:
            raise PostCreationError(r.status_code, r.text)
        urn = r.headers.get("x-restli-id")
        if not urn:
            raise PostCreationError(r.status_code, "resposta sem o header x-restli-id")
        return urn


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"
