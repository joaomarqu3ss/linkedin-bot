"""Rascunho de post: valida tudo antes da rede e publica exatamente o que foi revisto.

`prepare` concentra as regras (texto, visibilidade, imagens) e devolve um `Draft`
imutável. `publish` só aceita esse `Draft` e confere, antes do primeiro upload,
que as imagens no disco ainda são as mesmas do preview.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..auth.session import LinkedInAuth
from ..media.images import MAX_IMAGES_PER_POST, ImageInfo, inspect_image, upload_image
from ..transport.errors import DraftChangedError, InvalidPostError, TooManyImagesError
from .create import VISIBILITY, build_payload, create_post

Notify = Callable[[str], None]
Progress = Callable[[int, int, str], None]

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
class Draft:
    text: str
    visibility: str
    images: tuple[DraftImage, ...]

    def preview_payload(self) -> dict:
        """O payload que seria enviado, com autor e URNs de imagem simbólicos."""
        return build_payload(
            "urn:li:person:<AUTOR>",
            self.text,
            visibility=self.visibility,
            images=[
                (f"urn:li:image:<UPLOAD_{i}>", img.alt)
                for i, img in enumerate(self.images, start=1)
            ],
        )

    @property
    def fingerprint(self) -> str:
        """Identifica o conteúdo do post; o caminho dos arquivos fica de fora."""
        canonical = json.dumps(
            {
                "text": self.text,
                "visibility": self.visibility,
                "images": [{"sha256": i.sha256, "alt": i.alt} for i in self.images],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare(
    text: str,
    images: Iterable[tuple[str | Path, str | None]] = (),
    *,
    visibility: str = "public",
) -> Draft:
    """Valida texto, visibilidade e imagens sem tocar na rede."""
    text = text.strip()
    if not text:
        raise InvalidPostError("o texto do post está vazio")

    if visibility not in VISIBILITY:
        raise InvalidPostError(
            f"visibilidade inválida: '{visibility}'. "
            f"Use {' ou '.join(sorted(VISIBILITY))}."
        )

    pairs = list(images)
    if len(pairs) > MAX_IMAGES_PER_POST:
        raise TooManyImagesError(len(pairs), MAX_IMAGES_PER_POST)

    drafted = []
    for path, alt in pairs:
        info = inspect_image(path)
        drafted.append(DraftImage(info=info, alt=alt, sha256=_sha256(info.path)))

    return Draft(text=text, visibility=VISIBILITY[visibility], images=tuple(drafted))


def publish(
    auth: LinkedInAuth,
    draft: Draft,
    *,
    notify: Notify = lambda _: None,
    on_progress: Progress = lambda *_: None,
) -> str:
    """Sobe as imagens, cria o post e devolve o URN."""
    # Confere tudo antes do primeiro upload: um arquivo trocado no meio não pode
    # deixar metade das imagens enviadas para um post que não sai.
    for img in draft.images:
        try:
            current = _sha256(img.info.path)
        except OSError:
            raise DraftChangedError(str(img.info.path)) from None
        if current != img.sha256:
            raise DraftChangedError(str(img.info.path))

    n = len(draft.images)
    total = n + 1
    uploaded: list[tuple[str, str | None]] = []
    for i, img in enumerate(draft.images, start=1):
        uploaded.append((upload_image(auth, img.info, notify=notify), img.alt))
        on_progress(i, total, f"imagem {i}/{n} enviada")

    notify("\n→ publicando...")
    payload = build_payload(
        auth.person_urn, draft.text, visibility=draft.visibility, images=uploaded
    )
    urn = create_post(auth, payload)
    on_progress(total, total, "post publicado")
    return urn
