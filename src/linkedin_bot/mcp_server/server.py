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
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

import anyio
import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .. import __version__
from ..auth.session import LinkedInAuth
from ..auth.status import session_status
from ..posts.create import post_url
from ..posts.draft import Draft, prepare, publish
from ..transport import redact
from ..transport.errors import LinkedInBotError
from .previews import PreviewStore

INSTRUCTIONS = (
    "Publishes posts to the user's own LinkedIn profile. A post is live the moment it is "
    "published — LinkedIn has no drafts. Every post takes two calls: "
    "`linkedin_preview_post`, then show the user the returned text, images and warnings, "
    "and call `linkedin_publish_post` with that `preview_id` only after the user approves "
    "that exact preview. To change anything, preview again. When a tool reports a missing "
    "or expired session, tell the user to run `linkedin-bot auth login` in a terminal "
    "(it needs a browser)."
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


class ImageSummary(BaseModel):
    file: str
    width: int | None
    height: int | None
    size_kb: int
    alt: str | None


class PreviewResult(BaseModel):
    preview_id: str
    expires_in_seconds: int
    text: str
    visibility: str
    images: list[ImageSummary]
    payload: dict
    warnings: list[str]


class PublishResult(BaseModel):
    urn: str
    url: str
    visibility: str


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
    return msg


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_server(*, previews: PreviewStore | None = None) -> MCPServer:
    store = previews if previews is not None else PreviewStore()
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
            "publishing. Image paths must be absolute (JPG, PNG or GIF, up to 20). "
            "Hashtags and @mentions publish as plain text."
        ),
        annotations=READ_ONLY,
    )
    def linkedin_preview_post(
        text: str,
        images: list[ImageInput] = [],
        visibility: Literal["public", "connections"] = "public",
    ) -> PreviewResult:
        pairs = [(_absolute(i.path), i.alt) for i in images]
        with tool_errors():
            draft = prepare(text, pairs, visibility=visibility)
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
            payload=draft.preview_payload(),
            warnings=_warnings(draft.text),
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
            "Publish a previewed post to the user's LinkedIn profile. It goes live "
            "immediately. Call only after the user approved the exact preview, passing its "
            "`preview_id`. Each preview publishes at most once and expires after 15 "
            "minutes; preview again to change or retry."
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
        ctx: Context,
    ) -> PublishResult:
        with tool_errors():
            if confirmation is not None and not confirmation.confirm:
                raise ToolError("publicação cancelada pelo usuário")
            # Leitura local: sessão ruim falha aqui, antes de consumir o preview.
            auth = LinkedInAuth.load()
            # Daqui em diante o preview está consumido, mesmo que algo falhe.
            draft = store.take(preview_id)

            def bridge(done: int, total: int, message: str) -> None:
                anyio.from_thread.run(ctx.report_progress, done, total, message)

            urn = await anyio.to_thread.run_sync(
                partial(publish, auth, draft, on_progress=bridge)
            )
            return PublishResult(urn=urn, url=post_url(urn), visibility=draft.visibility)

    return server


def run() -> int:
    redact.install()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_server().run()
    return 0
