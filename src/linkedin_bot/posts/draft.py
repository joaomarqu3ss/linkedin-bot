"""Rascunho de post: valida tudo antes da rede e publica exatamente o que foi revisto.

`prepare` concentra as regras (texto, visibilidade, mídia) e devolve um `Draft`
imutável. `publish` só aceita esse `Draft` e confere, antes do primeiro upload,
que os arquivos no disco ainda são os mesmos do preview.

A mídia é um `attachment` só: imagens (1–20) ou um vídeo — a API não mistura, e o
tipo não deixa representar um post com os dois.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..auth.session import LinkedInAuth
from ..media.images import MAX_IMAGES_PER_POST, ImageInfo, inspect_image, upload_image
from ..media.videos import (
    UNKNOWN_DURATION_WARNING,
    VideoInfo,
    inspect_video,
    upload_video,
    wait_processed,
)
from ..transport.errors import (
    IMAGES_OR_VIDEO,
    DraftChangedError,
    InvalidPostError,
    TooManyImagesError,
    definitely_not_created,
)
from .create import VISIBILITY, build_payload, create_post, create_post_when_ready

Notify = Callable[[str], None]

# Fases de um publish, na ordem. A partir de PUBLISHING o POST /posts pode ter saído.
UPLOADING = "uploading"
PROCESSING = "processing"
PUBLISHING = "publishing"
PUBLISHED = "published"


@dataclass(frozen=True)
class Event:
    phase: str
    done: int = 0
    total: int = 0
    unit: str = ""          # "imagens" ou "bytes"
    urn: str | None = None  # só em PUBLISHED


Listener = Callable[[Event], None]

_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class DraftImage:
    info: ImageInfo
    alt: str | None
    sha256: str


@dataclass(frozen=True)
class Images:
    items: tuple[DraftImage, ...]


@dataclass(frozen=True)
class Video:
    info: VideoInfo
    title: str
    sha256: str


Attachment = Images | Video


@dataclass(frozen=True)
class Draft:
    text: str
    visibility: str
    attachment: Attachment | None = None

    @property
    def images(self) -> tuple[DraftImage, ...]:
        return self.attachment.items if isinstance(self.attachment, Images) else ()

    @property
    def video(self) -> Video | None:
        return self.attachment if isinstance(self.attachment, Video) else None

    @property
    def warnings(self) -> list[str]:
        """Avisos sobre a mídia que não bloqueiam o post."""
        if self.video and self.video.info.duration is None:
            return [UNKNOWN_DURATION_WARNING]
        return []

    def preview_payload(self) -> dict:
        """O payload que seria enviado, com autor e URNs de mídia simbólicos."""
        return build_payload(
            "urn:li:person:<AUTOR>",
            self.text,
            visibility=self.visibility,
            images=[
                (f"urn:li:image:<UPLOAD_{i}>", img.alt)
                for i, img in enumerate(self.images, start=1)
            ],
            video=("urn:li:video:<UPLOAD>", self.video.title) if self.video else None,
        )

    @property
    def fingerprint(self) -> str:
        """Identifica o conteúdo do post; o caminho dos arquivos fica de fora."""
        content: dict = {"text": self.text, "visibility": self.visibility}
        if self.video:
            content["video"] = {"sha256": self.video.sha256, "title": self.video.title}
        else:
            content["images"] = [{"sha256": i.sha256, "alt": i.alt} for i in self.images]
        canonical = json.dumps(
            content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_unchanged(self) -> None:
        """Falha se algum arquivo mudou (ou sumiu) desde o preview."""
        files = [(i.info.path, i.sha256) for i in self.images]
        if self.video:
            files.append((self.video.info.path, self.video.sha256))
        for path, expected in files:
            try:
                current = _sha256(path)
            except OSError:
                raise DraftChangedError(str(path)) from None
            if current != expected:
                raise DraftChangedError(str(path))


def prepare(
    text: str,
    images: Iterable[tuple[str | Path, str | None]] = (),
    *,
    visibility: str = "public",
    video: str | Path | None = None,
    video_title: str | None = None,
) -> Draft:
    """Valida texto, visibilidade e mídia sem tocar na rede."""
    text = text.strip()
    if not text:
        raise InvalidPostError("o texto do post está vazio")

    if visibility not in VISIBILITY:
        raise InvalidPostError(
            f"visibilidade inválida: '{visibility}'. "
            f"Use {' ou '.join(sorted(VISIBILITY))}."
        )

    pairs = list(images)
    if pairs and video is not None:
        raise InvalidPostError(IMAGES_OR_VIDEO)
    if len(pairs) > MAX_IMAGES_PER_POST:
        raise TooManyImagesError(len(pairs), MAX_IMAGES_PER_POST)

    attachment: Attachment | None = None
    if video is not None:
        info = inspect_video(video)
        title = (video_title or "").strip() or info.path.stem
        attachment = Video(info=info, title=title, sha256=_sha256(info.path))
    elif pairs:
        drafted = []
        for path, alt in pairs:
            img = inspect_image(path)
            drafted.append(DraftImage(info=img, alt=alt, sha256=_sha256(img.path)))
        attachment = Images(tuple(drafted))

    return Draft(text=text, visibility=VISIBILITY[visibility], attachment=attachment)


def publish(
    auth: LinkedInAuth,
    draft: Draft,
    *,
    notify: Notify = lambda _: None,
    on_event: Listener = lambda _: None,
) -> str:
    """Sobe a mídia, cria o post e devolve o URN."""
    # Confere tudo antes do primeiro upload: um arquivo trocado no meio não pode
    # deixar metade da mídia enviada para um post que não sai.
    draft.verify_unchanged()

    images: list[tuple[str, str | None]] = []
    video: tuple[str, str] | None = None
    ready = True

    n = len(draft.images)
    for i, img in enumerate(draft.images, start=1):
        images.append((upload_image(auth, img.info, notify=notify), img.alt))
        on_event(Event(UPLOADING, i, n, "imagens"))

    if draft.video:
        size = draft.video.info.size_bytes
        on_event(Event(UPLOADING, 0, size, "bytes"))
        urn = upload_video(
            auth,
            draft.video.info,
            notify=notify,
            on_bytes=lambda done, total: on_event(Event(UPLOADING, done, total, "bytes")),
        )
        video = (urn, draft.video.title)
        on_event(Event(PROCESSING))
        notify("  → aguardando o processamento do vídeo na LinkedIn...")

    posting = False
    try:
        if video:
            ready = wait_processed(auth, video[0], notify=notify) is not None

        notify("\n→ publicando...")
        payload = build_payload(
            auth.person_urn, draft.text, visibility=draft.visibility, images=images, video=video
        )
        posting = True
        if ready:
            on_event(Event(PUBLISHING))
            post = create_post(auth, payload)
        else:
            post = create_post_when_ready(
                auth,
                payload,
                notify=notify,
                on_attempt=lambda: on_event(Event(PUBLISHING)),
                on_wait=lambda: on_event(Event(PROCESSING)),
            )
    except Exception as exc:
        # Falha ambígua na criação: o post pode estar no ar com este vídeo.
        if video and (not posting or definitely_not_created(exc)):
            note = f"o vídeo enviado ficou sem uso na LinkedIn: {video[0]}"
            if video[0] not in str(exc):
                exc.add_note(note)
                notify(f"  {note}")
        raise

    on_event(Event(PUBLISHED, urn=post))
    return post
