"""Upload de imagens: initializeUpload → PUT do binário → URN pronto para o post."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from ..auth.session import LinkedInAuth
from ..transport.errors import (
    ImageNotFoundError,
    ImageTooLargeError,
    UnsupportedImageFormatError,
    UploadFailedError,
)
from .inspect import dimensions

MAX_PIXELS = 36_152_320
ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")
MAX_IMAGES_PER_POST = 20

Notify = Callable[[str], None]


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    size_bytes: int
    width: int | None = None
    height: int | None = None

    @property
    def pixels(self) -> int | None:
        if self.width and self.height:
            return self.width * self.height
        return None

    def describe(self) -> str:
        dims = f"{self.width}x{self.height}" if self.width else "dimensões ?"
        return f"{self.path.name} ({dims}, {self.size_bytes / 1024:.0f} KB)"


def inspect_image(raw_path: str | Path) -> ImageInfo:
    """Valida existência, formato e contagem de pixels antes de gastar upload."""
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise ImageNotFoundError(str(path))

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UnsupportedImageFormatError(str(path), suffix, ALLOWED_SUFFIXES)

    dims = dimensions(path)
    info = ImageInfo(
        path=path,
        size_bytes=path.stat().st_size,
        width=dims[0] if dims else None,
        height=dims[1] if dims else None,
    )
    if info.pixels and info.pixels > MAX_PIXELS:
        raise ImageTooLargeError(str(path), info.pixels, MAX_PIXELS)
    return info


def initialize_upload(auth: LinkedInAuth) -> tuple[str, str]:
    """Registra o upload. Devolve (upload_url, image_urn)."""
    with auth.api_client() as client:
        r = client.post(
            "/images?action=initializeUpload",
            json={"initializeUploadRequest": {"owner": auth.person_urn}},
        )
        if r.status_code != 200:
            raise UploadFailedError("initializeUpload", r.status_code, r.text)
        value = r.json()["value"]
        return value["uploadUrl"], value["image"]


def upload_binary(auth: LinkedInAuth, upload_url: str, info: ImageInfo) -> None:
    """Envia o arquivo em streaming — nunca carrega a imagem inteira na memória."""
    with auth.upload_client() as client, info.path.open("rb") as fh:
        r = client.put(
            upload_url,
            content=fh,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(info.size_bytes),
            },
        )
    if r.status_code not in (200, 201):
        raise UploadFailedError(str(info.path), r.status_code, r.text)


def check_status(auth: LinkedInAuth, image_urn: str) -> str | None:
    """Lê o status do asset. Devolve None se o token não puder ler.

    Em chamadas versionadas, `w_member_social` é write-only para /rest/images:
    um token só com esse escopo recebe 403 no GET. Nesse caso seguimos sem
    confirmar o processamento — a LinkedIn o conclui de forma assíncrona.
    """
    with auth.api_client() as client:
        r = client.get(f"/images/{quote(image_urn, safe='')}")
        if r.status_code == 403:
            return None
        if r.status_code != 200:
            return None
        return r.json().get("status")


def wait_available(
    auth: LinkedInAuth,
    image_urn: str,
    *,
    attempts: int = 6,
    delay: float = 1.5,
    notify: Notify = lambda _: None,
) -> str | None:
    status = check_status(auth, image_urn)
    if status is None:
        notify("  (status do asset não legível com este escopo — seguindo)")
        return None

    for _ in range(attempts):
        if status in ("AVAILABLE", "PROCESSING_FAILED"):
            return status
        time.sleep(delay)
        status = check_status(auth, image_urn)
        if status is None:
            return None
    return status


def upload_image(
    auth: LinkedInAuth, info: ImageInfo, *, notify: Notify = lambda _: None
) -> str:
    """Sobe uma imagem já inspecionada e devolve o `urn:li:image:{id}`."""
    notify(f"  → registrando upload de {info.describe()}")
    upload_url, image_urn = initialize_upload(auth)

    notify(f"  → enviando binário ({info.size_bytes / 1024:.0f} KB)")
    upload_binary(auth, upload_url, info)

    status = wait_available(auth, image_urn, notify=notify)
    if status == "PROCESSING_FAILED":
        raise UploadFailedError(
            str(info.path), 200, "a LinkedIn rejeitou a imagem no processamento"
        )

    notify(f"  ✓ {image_urn}" + (f" [{status}]" if status else ""))
    return image_urn
