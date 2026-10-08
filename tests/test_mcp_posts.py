"""linkedin_preview_post / linkedin_publish_post: preview de uso único e confirmação."""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest
from mcp import types
from mcp.client import Client

from linkedin_bot.mcp_server import server as server_mod
from linkedin_bot.mcp_server.jobs import ACTIVE
from linkedin_bot.mcp_server.previews import PreviewStore
from linkedin_bot.mcp_server.server import build_server
from linkedin_bot.media.videos import UNKNOWN_DURATION_WARNING
from linkedin_bot.posts.draft import prepare
from linkedin_bot.posts.little import escape
from linkedin_bot.transport.errors import TokenExpiredError, UploadFailedError

pytestmark = pytest.mark.anyio

MODES = ["auto", "legacy"]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def calls(monkeypatch):
    log = SimpleNamespace(uploads=[], posts=[], loads=0, load_error=None)

    def fake_upload(auth, info, notify=None):
        log.uploads.append(info.path)
        return f"urn:li:image:{len(log.uploads)}"

    def fake_create(auth, payload):
        log.posts.append(payload)
        return f"urn:li:share:{len(log.posts)}"

    def fake_load(store=None):
        log.loads += 1
        if log.load_error:
            raise log.load_error
        return SimpleNamespace(person_urn="urn:li:person:abc")

    monkeypatch.setattr("linkedin_bot.posts.draft.upload_image", fake_upload)
    monkeypatch.setattr("linkedin_bot.posts.draft.create_post", fake_create)
    monkeypatch.setattr(server_mod.LinkedInAuth, "load", staticmethod(fake_load))
    return log


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def previews(clock):
    return PreviewStore(now=clock)


def answer(action="accept", confirm=True, seen=None):
    async def cb(context, params):
        if seen is not None:
            seen.append(params)
        return types.ElicitResult(
            action=action, content={"confirm": confirm} if action == "accept" else None
        )

    return cb


async def preview(c, text="Olá, mundo", **kw):
    return await c.call_tool("linkedin_preview_post", {"text": text, **kw})


# --- annotations ---------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_annotations(mode, previews):
    async with Client(build_server(previews=previews), mode=mode) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    def flags(name):
        a = tools[name].annotations
        return (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)

    assert flags("linkedin_preview_post") == (True, False, True, False)
    assert flags("linkedin_publish_post") == (False, True, False, True)
    assert flags("linkedin_publish_status") == (True, False, False, False)


# --- preview -------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_preview_id_deterministico_e_payload(mode, previews, make_png):
    img = make_png()
    args = {"images": [{"path": str(img), "alt": "foto"}]}
    async with Client(build_server(previews=previews), mode=mode) as c:
        r1 = await preview(c, **args)
        r2 = await preview(c, **args)
    assert not r1.is_error
    s = r1.structured_content
    assert s["preview_id"] == r2.structured_content["preview_id"]
    assert s["expires_in_seconds"] == 900
    assert s["text"] == "Olá, mundo" and s["visibility"] == "PUBLIC"
    draft = prepare("Olá, mundo", [(img, "foto")])
    assert s["preview_id"] == draft.fingerprint
    assert s["payload"] == draft.preview_payload()
    assert s["images"] == [
        {"file": img.name, "width": 1200, "height": 800,
         "size_kb": round(img.stat().st_size / 1024), "alt": "foto"}
    ]
    assert s["warnings"] == []
    assert s["video"] is None


@pytest.mark.parametrize("mode", MODES)
async def test_preview_visibilidade_connections(mode, previews):
    async with Client(build_server(previews=previews), mode=mode) as c:
        r = await preview(c, visibility="connections")
    assert r.structured_content["visibility"] == "CONNECTIONS"


@pytest.mark.parametrize("mode", MODES)
async def test_preview_warnings(mode, previews):
    async with Client(build_server(previews=previews), mode=mode) as c:
        both = (await preview(c, "Veja #python com @ana")).structured_content["warnings"]
        email = (await preview(c, "escreva para ana@exemplo.com")).structured_content["warnings"]
    assert both == [
        "hashtags (#tag) serão publicadas como texto, sem link",
        "menções (@nome) serão publicadas como texto, sem link",
    ]
    assert email == []


@pytest.mark.parametrize("mode", MODES)
async def test_preview_erros(mode, previews, make_png, tmp_path):
    async with Client(build_server(previews=previews), mode=mode) as c:
        relativo = await preview(c, images=[{"path": "rel/a.png"}])
        inexistente = await preview(c, images=[{"path": str(tmp_path / "x.png")}])
        img = make_png()
        muitas = await preview(c, images=[{"path": str(img)}] * 21)
    assert relativo.is_error and "use caminho absoluto: rel/a.png" in relativo.content[0].text
    assert inexistente.is_error
    assert muitas.is_error


@pytest.mark.parametrize("mode", MODES)
async def test_preview_nao_exige_sessao_nem_rede(mode, previews, calls):
    async with Client(build_server(previews=previews), mode=mode) as c:
        r = await preview(c)
    assert not r.is_error
    assert calls.loads == 0 and calls.posts == [] and calls.uploads == []


# --- publish -------------------------------------------------------------------


async def preview_id(c, **kw) -> str:
    return (await preview(c, **kw)).structured_content["preview_id"]


async def follow(c, job_id: str) -> dict:
    """Acompanha o job como um agente faria, até uma fase terminal."""
    for _ in range(500):
        r = await c.call_tool("linkedin_publish_status", {"job_id": job_id})
        assert not r.is_error, r.content
        st = r.structured_content
        if st["phase"] not in ACTIVE:
            assert st["poll_after_seconds"] is None
            return st
        assert st["poll_after_seconds"] > 0
        await anyio.sleep(0.01)
    raise AssertionError("o job não terminou")


async def publish(c, pid: str) -> dict:
    r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
    assert not r.is_error, r.content
    return await follow(c, r.structured_content["job_id"])


@pytest.mark.parametrize("mode", MODES)
async def test_publish_accept(mode, previews, calls, make_png):
    img = make_png()
    seen = []

    async with Client(
        build_server(previews=previews), mode=mode, elicitation_callback=answer(seen=seen)
    ) as c:
        pid = await preview_id(c, text="Olá #x", images=[{"path": str(img), "alt": "a"}])
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
        assert not r.is_error
        started = r.structured_content
        assert started["phase"] == "queued" and started["preview_id"] == pid
        assert started["poll_after_seconds"] == 5
        st = await follow(c, started["job_id"])
    assert st["phase"] == "published"
    assert st["urn"] == "urn:li:share:1"
    assert st["url"] == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    assert st["visibility"] == "PUBLIC" and st["error"] is None
    assert len(calls.posts) == 1 and calls.posts[0]["commentary"] == escape("Olá #x")
    assert calls.posts[0]["author"] == "urn:li:person:abc"
    assert len(seen) == 1
    assert "Publicar no LinkedIn agora? Visibilidade: Público" in seen[0].message
    assert "Olá #x" in seen[0].message and f"Imagens: {img.name}" in seen[0].message


@pytest.mark.parametrize("mode", MODES)
async def test_confirmacao_sem_imagens_omite_a_linha(mode, previews, calls):
    seen = []
    async with Client(
        build_server(previews=previews), mode=mode, elicitation_callback=answer(seen=seen)
    ) as c:
        pid = await preview_id(c, visibility="connections")
        await publish(c, pid)
    assert "Imagens" not in seen[0].message and "Vídeo" not in seen[0].message
    assert "Visibilidade: Conexões" in seen[0].message


@pytest.mark.parametrize("mode", MODES)
async def test_publish_confirm_false_mantem_o_preview(mode, previews, calls):
    verdict = {"confirm": False}

    async def cb(context, params):
        return types.ElicitResult(action="accept", content=verdict)

    async with Client(build_server(previews=previews), mode=mode, elicitation_callback=cb) as c:
        pid = await preview_id(c)
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
        assert r.is_error and "cancelada" in r.content[0].text
        assert calls.posts == [] and calls.loads == 0
        verdict["confirm"] = True
        st = await publish(c, pid)
    assert st["phase"] == "published" and len(calls.posts) == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_publish_decline(mode, action, previews, calls):
    async with Client(
        build_server(previews=previews), mode=mode, elicitation_callback=answer(action)
    ) as c:
        pid = await preview_id(c)
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
    assert r.is_error
    assert calls.posts == [] and calls.uploads == []


@pytest.mark.parametrize("mode", MODES)
async def test_publish_sem_elicitation_publica(mode, previews, calls):
    async with Client(build_server(previews=previews), mode=mode) as c:
        st = await publish(c, await preview_id(c))
    assert st["phase"] == "published" and len(calls.posts) == 1


@pytest.mark.parametrize("mode", MODES)
async def test_publish_segunda_vez_falha(mode, previews, calls):
    async with Client(build_server(previews=previews), mode=mode) as c:
        pid = await preview_id(c)
        await publish(c, pid)
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
    assert r.is_error and "preview desconhecido ou expirado" in r.content[0].text
    assert len(calls.posts) == 1


@pytest.mark.parametrize("mode", MODES)
async def test_publish_id_desconhecido(mode, previews, calls):
    async with Client(build_server(previews=previews), mode=mode) as c:
        r = await c.call_tool("linkedin_publish_post", {"preview_id": "nope"})
    assert r.is_error and "preview desconhecido ou expirado" in r.content[0].text
    assert calls.posts == []


@pytest.mark.parametrize("mode", MODES)
async def test_publish_preview_vencido(mode, previews, calls, clock):
    async with Client(build_server(previews=previews), mode=mode) as c:
        pid = await preview_id(c)
        clock.t += 901
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
    assert r.is_error and "expirado" in r.content[0].text
    assert calls.posts == []


@pytest.mark.parametrize("mode", MODES)
async def test_publish_arquivo_alterado_nao_consome(mode, previews, calls, make_png):
    img = make_png()
    async with Client(build_server(previews=previews), mode=mode) as c:
        pid = await preview_id(c, images=[{"path": str(img)}])
        img.write_bytes(img.read_bytes() + b"mudou")
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
    assert r.is_error and "mudou depois do preview" in r.content[0].text
    assert calls.posts == [] and calls.uploads == []
    assert previews.get(pid) is not None


@pytest.mark.parametrize("mode", MODES)
async def test_publish_sessao_expirada_preserva_o_preview(mode, previews, calls):
    calls.load_error = TokenExpiredError()
    async with Client(build_server(previews=previews), mode=mode) as c:
        pid = await preview_id(c)
        r = await c.call_tool("linkedin_publish_post", {"preview_id": pid})
        assert r.is_error and "linkedin-bot auth login" in r.content[0].text
        assert previews.get(pid) is not None
        calls.load_error = None
        st = await publish(c, pid)
    assert st["phase"] == "published" and len(calls.posts) == 1


@pytest.mark.parametrize("mode", MODES)
async def test_falha_no_upload_devolve_o_preview(mode, previews, calls, make_png, monkeypatch):
    def broken_upload(auth, info, notify=None):
        raise UploadFailedError(str(info.path), 500, "boom")

    monkeypatch.setattr("linkedin_bot.posts.draft.upload_image", broken_upload)
    img = make_png()
    async with Client(build_server(previews=previews), mode=mode) as c:
        pid = await preview_id(c, images=[{"path": str(img)}])
        st = await publish(c, pid)
        assert st["phase"] == "failed" and st["preview_released"]
        assert "Falha ao enviar" in st["error"]
        monkeypatch.setattr("linkedin_bot.posts.draft.upload_image", lambda *a, **k: "urn:li:image:1")
        again = await publish(c, pid)
    assert again["phase"] == "published"


@pytest.mark.parametrize("mode", MODES)
async def test_status_job_desconhecido(mode, previews):
    async with Client(build_server(previews=previews), mode=mode) as c:
        r = await c.call_tool("linkedin_publish_status", {"job_id": "f" * 32})
    assert r.is_error and "confer" in r.content[0].text


# --- vídeo ---------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_preview_video(mode, previews, make_mp4):
    path = make_mp4("demo.mp4", seconds=None)
    async with Client(build_server(previews=previews), mode=mode) as c:
        r = await preview(c, video={"path": str(path), "title": "Lançamento"})
    assert not r.is_error, r.content
    s = r.structured_content
    assert s["images"] == []
    assert s["video"] == {
        "file": "demo.mp4", "title": "Lançamento", "size_mb": 0.1, "duration_seconds": None
    }
    assert s["payload"]["content"] == {
        "media": {"id": "urn:li:video:<UPLOAD>", "title": "Lançamento"}
    }
    assert s["warnings"] == [UNKNOWN_DURATION_WARNING]


@pytest.mark.parametrize("mode", MODES)
async def test_preview_video_erros(mode, previews, make_mp4, make_png):
    async with Client(build_server(previews=previews), mode=mode) as c:
        ambos = await preview(
            c, images=[{"path": str(make_png())}], video={"path": str(make_mp4())}
        )
        relativo = await preview(c, video={"path": "rel/v.mp4"})
        mov = await preview(c, video={"path": str(make_mp4("v.mov"))})
    assert ambos.is_error and "imagens ou um vídeo" in ambos.content[0].text
    assert relativo.is_error and "caminho absoluto" in relativo.content[0].text
    assert mov.is_error and "(aceito: .mp4)" in mov.content[0].text


@pytest.mark.parametrize("mode", MODES)
async def test_publish_video(mode, previews, calls, make_mp4, monkeypatch):
    def fake_upload_video(auth, info, *, notify=lambda _: None, on_bytes=lambda *_: None):
        on_bytes(info.size_bytes, info.size_bytes)
        return "urn:li:video:V1"

    monkeypatch.setattr("linkedin_bot.posts.draft.upload_video", fake_upload_video)
    monkeypatch.setattr(
        "linkedin_bot.posts.draft.wait_processed", lambda *a, **k: "AVAILABLE"
    )
    seen = []
    async with Client(
        build_server(previews=previews), mode=mode, elicitation_callback=answer(seen=seen)
    ) as c:
        pid = await preview_id(c, video={"path": str(make_mp4("demo.mp4")), "title": "Demo"})
        st = await publish(c, pid)
    assert st["phase"] == "published"
    assert st["unit"] == "bytes" and st["done"] == st["total"] == 80 * 1024
    assert calls.posts[0]["content"] == {"media": {"id": "urn:li:video:V1", "title": "Demo"}}
    assert "Vídeo: demo.mp4 (título: Demo)" in seen[0].message
