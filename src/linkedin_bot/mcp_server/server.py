"""Servidor MCP sobre o mesmo núcleo da CLI.

No stdio o stdout é o canal JSON-RPC: tudo que o servidor diz vai para o stderr.

Sem `from __future__ import annotations` de propósito: o SDK avalia as anotações das tools,
e `Resolve(ask_confirmation)` referencia uma função local de `build_server`.
"""

import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import anyio.to_thread
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .. import __version__, config
from ..auth.session import LinkedInAuth
from ..auth.status import session_status
from ..posts.draft import Draft, prepare
from ..transport import redact
from ..transport.errors import LinkedInBotError
from .jobs import ACTIVE, JobManager, JobRecord
from .previews import UNKNOWN_PREVIEW, PreviewStore

INSTRUCTIONS = (
    "Publishes posts to the user's own LinkedIn profile. A post is live the moment it is "
    "published — LinkedIn has no drafts. Every post takes three steps: "
    "`linkedin_preview_post`, then show the user the returned text, media and warnings; "
    "only after the user approves that exact preview, call `linkedin_publish_post` with its "
    "`preview_id`. Publishing runs in the background and returns a `job_id`: call "
    "`linkedin_publish_status` with it, waiting `poll_after_seconds` between calls, until "
    "the phase is `published` (report the URL), `failed` (report the error; if "
    "`preview_released` is true the same `preview_id` can be published again) or "
    "`interrupted`. If the phase is `uncertain`, the post may already be live: never "
    "preview and publish the same content again — ask the user to check their profile. "
    "To change anything, preview again. When a tool reports a missing or expired session, "
    "tell the user to run `linkedin-bot auth login` in a terminal (it needs a browser)."
)


READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@contextmanager
def tool_errors() -> Iterator[None]:
    """Converte `LinkedInBotError` em `ToolError`, preservando a mensagem que ensina a corrigir."""
    try:
        yield
    except LinkedInBotError as exc:
        raise ToolError(str(exc)) from exc


class StatusResult(BaseModel):
    configured: bool
    client_id: str | None
    authenticated: bool
    name: str | None
    person_urn: str | None
    expires_at: str | None
    days_remaining: int | None
    problem: str | None
    fix: str | None


class ImageInput(BaseModel):
    path: str = Field(description="Absolute path to a JPG, PNG or GIF file")
    alt: str | None = None


class VideoInput(BaseModel):
    path: str = Field(description="Absolute path to an MP4 file (3 s to 30 min, up to 500 MB)")
    title: str | None = Field(
        default=None, description="Title shown with the video; defaults to the file name"
    )


class ImageSummary(BaseModel):
    file: str
    width: int | None
    height: int | None
    size_kb: int
    alt: str | None


class VideoSummary(BaseModel):
    file: str
    title: str
    size_mb: float
    duration_seconds: float | None


class PreviewResult(BaseModel):
    preview_id: str
    expires_in_seconds: int
    text: str
    visibility: str
    images: list[ImageSummary]
    video: VideoSummary | None
    payload: dict
    warnings: list[str]


class JobStatus(BaseModel):
    job_id: str
    phase: Literal[
        "queued", "uploading", "processing", "publishing",
        "published", "failed", "interrupted", "uncertain",
    ]
    visibility: str
    done: int
    total: int
    unit: str
    message: str
    urn: str | None
    url: str | None
    error: str | None
    preview_id: str
    preview_released: bool
    poll_after_seconds: int | None


def job_status(rec: JobRecord) -> JobStatus:
    return JobStatus(
        job_id=rec.job_id,
        phase=rec.phase,  # type: ignore[arg-type]
        visibility=rec.visibility,
        done=rec.done,
        total=rec.total,
        unit=rec.unit,
        message=rec.message,
        urn=rec.urn,
        url=rec.url,
        error=rec.error,
        preview_id=rec.preview_id,
        preview_released=rec.preview_released,
        poll_after_seconds=config.POLL_AFTER.get(rec.phase) if rec.phase in ACTIVE else None,
    )


UNKNOWN_JOB = (
    f"job desconhecido ou expirado (registros duram {config.JOB_RECORD_TTL / 3600:g} h). "
    "O post pode ter sido publicado: peça ao usuário para conferir o perfil antes de "
    "tentar de novo"
)


class PublishConfirmation(BaseModel):
    confirm: bool = Field(description="Publicar agora")


_HASHTAG = re.compile(r"(?<!\w)#\w")
_MENTION = re.compile(r"(?<!\w)@\w")
_VISIBILITY_LABEL = {"PUBLIC": "Público", "CONNECTIONS": "Conexões"}


def _warnings(text: str) -> list[str]:
    out = []
    if _HASHTAG.search(text):
        out.append("hashtags (#tag) serão publicadas como texto, sem link")
    if _MENTION.search(text):
        out.append("menções (@nome) serão publicadas como texto, sem link")
    return out


def _absolute(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ToolError(f"use caminho absoluto: {raw}")
    return path


def confirmation_message(draft: Draft) -> str:
    msg = (
        f"Publicar no LinkedIn agora? "
        f"Visibilidade: {_VISIBILITY_LABEL.get(draft.visibility, draft.visibility)}\n\n{draft.text}"
    )
    if draft.images:
        msg += "\n\nImagens: " + ", ".join(i.info.path.name for i in draft.images)
    if draft.video:
        msg += f"\n\nVídeo: {draft.video.info.path.name} (título: {draft.video.title})"
    return msg


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_server(
    *, previews: PreviewStore | None = None, jobs: JobManager | None = None
) -> MCPServer:
    store = previews if previews is not None else PreviewStore()
    manager = jobs if jobs is not None else JobManager(store)
    server = MCPServer(name="linkedin-bot", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(
        name="linkedin_status",
        title="LinkedIn session status",
        description=(
            "Report whether linkedin-bot can publish: app configured, session valid, "
            "author, and days until the token expires. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def linkedin_status() -> StatusResult:
        with tool_errors():
            s = session_status()
            return StatusResult(**{**asdict(s), "expires_at": _iso(s.expires_at)})

    @server.tool(
        name="linkedin_preview_post",
        title="Preview a LinkedIn post",
        description=(
            "Validate a LinkedIn post and register it for publishing; nothing is sent to "
            "LinkedIn. Returns the exact text, images and payload that would be published, "
            "plus a `preview_id`. Show the user the text, images and warnings before "
            "publishing. A post carries either images (absolute paths; JPG, PNG or GIF, up "
            "to 20) or one video (absolute path to an MP4), never both. "
            "Hashtags and @mentions publish as plain text."
        ),
        annotations=READ_ONLY,
    )
    def linkedin_preview_post(
        text: str,
        images: list[ImageInput] = [],
        video: VideoInput | None = None,
        visibility: Literal["public", "connections"] = "public",
    ) -> PreviewResult:
        pairs = [(_absolute(i.path), i.alt) for i in images]
        video_path = _absolute(video.path) if video else None
        with tool_errors():
            draft = prepare(
                text,
                pairs,
                visibility=visibility,
                video=video_path,
                video_title=video.title if video else None,
            )
        return PreviewResult(
            preview_id=store.put(draft),
            expires_in_seconds=int(store.ttl),
            text=draft.text,
            visibility=draft.visibility,
            images=[
                ImageSummary(
                    file=i.info.path.name,
                    width=i.info.width,
                    height=i.info.height,
                    size_kb=round(i.info.size_bytes / 1024),
                    alt=i.alt,
                )
                for i in draft.images
            ],
            video=VideoSummary(
                file=draft.video.info.path.name,
                title=draft.video.title,
                size_mb=round(draft.video.info.size_bytes / 1024 / 1024, 1),
                duration_seconds=draft.video.info.duration,
            ) if draft.video else None,
            payload=draft.preview_payload(),
            warnings=_warnings(draft.text) + draft.warnings,
        )

    def ask_confirmation(preview_id: str, ctx: Context) -> Elicit | None:
        # Roda mais de uma vez no protocolo 2026-07-28: sem efeito colateral, só `get`.
        draft = store.get(preview_id)
        caps = ctx.client_capabilities
        if draft is None or not (caps and caps.elicitation):
            return None  # sem preview o corpo responde; sem elicitation vale a permissão do cliente
        return Elicit(confirmation_message(draft), PublishConfirmation)

    @server.tool(
        name="linkedin_publish_post",
        title="Publish a previewed LinkedIn post",
        description=(
            "Publish a previewed post to the user's LinkedIn profile. It goes live as soon "
            "as the upload finishes. Call only after the user approved the exact preview, "
            "passing its `preview_id`. Returns at once with a `job_id`; follow it with "
            "`linkedin_publish_status`. Each preview publishes at most once and expires "
            "after 15 minutes; preview again to change anything. One publish at a time."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def linkedin_publish_post(
        preview_id: str,
        confirmation: Annotated[PublishConfirmation | None, Resolve(ask_confirmation)],
    ) -> JobStatus:
        with tool_errors():
            if confirmation is not None and not confirmation.confirm:
                raise ToolError("publicação cancelada pelo usuário")
            # Tudo que pode falhar sem rede falha aqui, antes de consumir o preview.
            auth = LinkedInAuth.load()
            manager.ensure_idle()
            draft = store.get(preview_id)
            if draft is None:
                raise ToolError(UNKNOWN_PREVIEW)
            await anyio.to_thread.run_sync(draft.verify_unchanged)
            return job_status(manager.start(auth, preview_id))

    @server.tool(
        name="linkedin_publish_status",
        title="LinkedIn publish status",
        description=(
            "Report the progress of a publish started by `linkedin_publish_post`: phase, "
            "uploaded amount, and the post URL once published. Returns at once; call again "
            "after `poll_after_seconds` while the phase is queued, uploading, processing or "
            "publishing. Read-only."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def linkedin_publish_status(job_id: str) -> JobStatus:
        rec = manager.status(job_id)
        if rec is None:
            raise ToolError(UNKNOWN_JOB)
        return job_status(rec)

    return server


def run() -> int:
    redact.install()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_server().run()
    return 0
