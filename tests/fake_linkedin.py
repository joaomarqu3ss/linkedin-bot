"""Uma LinkedIn de mentira sobre `httpx.MockTransport`, com o RetryTransport real no meio.

Cobre o que o upload de vídeo e a criação de post tocam: initializeUpload, PUT das
partes, finalizeUpload, GET do status e POST /posts. Cada rota consulta uma fila de
respostas programadas pelo teste antes de cair no comportamento padrão.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from urllib.parse import unquote

import httpx

from linkedin_bot.config import API_BASE
from linkedin_bot.transport.retry import RetryTransport

VIDEO_URN = "urn:li:video:C5505AQH"
UPLOAD_HOST = "https://www.linkedin.com/dms-uploads"


class FakeLinkedIn:
    def __init__(self, part_size: int = 32 * 1024) -> None:
        self.part_size = part_size
        self.person_urn = "urn:li:person:abc"
        self.name = "Fulano"
        self.requests: list[httpx.Request] = []
        self.parts: dict[int, bytes] = {}
        self.initialized: dict | None = None
        self.finalized: dict | None = None
        self.posts: list[dict] = []
        self.statuses: deque = deque()     # sequência de status do GET de vídeo
        self.queued: dict[str, deque] = defaultdict(deque)  # rota → respostas forçadas

    # --- programação ---------------------------------------------------------

    def queue(self, route: str, *responses: httpx.Response) -> None:
        self.queued[route].extend(responses)

    # --- interface de LinkedInAuth -------------------------------------------

    def api_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=API_BASE, transport=RetryTransport(httpx.MockTransport(self._handle))
        )

    def upload_client(self, *, send_authorization: bool = True) -> httpx.Client:
        return httpx.Client(transport=RetryTransport(httpx.MockTransport(self._handle)))

    # --- rotas ---------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self._route(request)
        if self.queued[route]:
            return self.queued[route].popleft()
        return getattr(self, f"_{route}")(request)

    @staticmethod
    def _route(request: httpx.Request) -> str:
        url = str(request.url)
        if url.startswith(UPLOAD_HOST):
            return "part"
        if "action=initializeUpload" in url:
            return "initialize"
        if "action=finalizeUpload" in url:
            return "finalize"
        if request.url.path.startswith("/rest/videos/"):
            return "status"
        if request.url.path == "/rest/posts":
            return "post"
        raise AssertionError(f"rota inesperada: {request.method} {url}")

    def _initialize(self, request: httpx.Request) -> httpx.Response:
        self.initialized = json.loads(request.content)
        size = self.initialized["initializeUploadRequest"]["fileSizeBytes"]
        parts = [
            {
                "uploadUrl": f"{UPLOAD_HOST}/{VIDEO_URN}/part/{i}",
                "firstByte": first,
                "lastByte": min(first + self.part_size, size) - 1,
            }
            for i, first in enumerate(range(0, size, self.part_size))
        ]
        return httpx.Response(
            200,
            json={"value": {"video": VIDEO_URN, "uploadToken": "tok", "uploadInstructions": parts}},
        )

    def _part(self, request: httpx.Request) -> httpx.Response:
        index = int(request.url.path.rsplit("/", 1)[1])
        self.parts[index] = request.content
        return httpx.Response(200, headers={"etag": f'"etag-{index}"'})

    def _finalize(self, request: httpx.Request) -> httpx.Response:
        self.finalized = json.loads(request.content)
        return httpx.Response(200)

    def _status(self, request: httpx.Request) -> httpx.Response:
        assert unquote(request.url.path.rsplit("/", 1)[1]) == VIDEO_URN
        status = self.statuses.popleft() if len(self.statuses) > 1 else (
            self.statuses[0] if self.statuses else "AVAILABLE"
        )
        if isinstance(status, httpx.Response):
            return status
        body = {"id": VIDEO_URN, "status": status}
        if status == "PROCESSING_FAILED":
            body["processingFailureReason"] = "codec não suportado"
        return httpx.Response(200, json=body)

    def _post(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(json.loads(request.content))
        return httpx.Response(201, headers={"x-restli-id": f"urn:li:share:{len(self.posts)}"})


class Clock:
    """Relógio manual: `sleep` só avança o tempo."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
