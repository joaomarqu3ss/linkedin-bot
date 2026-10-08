"""Subcomando `linkedin-bot post`."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import TextIO

from ..auth.session import LinkedInAuth
from ..posts.create import VISIBILITY, post_url
from ..posts.draft import PUBLISHING, UPLOADING, Event, prepare, publish
from ..transport.errors import InvalidPostError
from .ui import ARROW, WARN, err

_MB = 1024 * 1024


def _out(msg: str = "") -> None:
    print(msg, file=sys.stdout)


def _resolve_text(args: argparse.Namespace) -> str:
    if args.from_file and args.text is not None:
        raise SystemExit("erro: use TEXTO ou --from-file, não os dois")

    if args.from_file:
        path = Path(args.from_file).expanduser()
        if not path.is_file():
            raise SystemExit(f"erro: arquivo não encontrado: {path}")
        text = path.read_text(encoding="utf-8")
    elif args.text == "-":
        text = sys.stdin.read()
    elif args.text is not None:
        text = args.text
    else:
        raise SystemExit(
            "erro: informe o texto do post, --from-file ARQUIVO, ou '-' para stdin"
        )
    return text


def _pair_alts(images: list[str], alts: list[str]) -> list[str | None]:
    """Um --alt vale para todas; N --alt casam por ordem com os --image."""
    if not alts:
        return [None] * len(images)
    if len(alts) == 1:
        return [alts[0]] * len(images)
    if len(alts) == len(images):
        return list(alts)
    raise SystemExit(
        f"erro: {len(alts)} --alt para {len(images)} --image. "
        "Informe um --alt (vale para todas) ou um por imagem."
    )


class UploadProgress:
    """Progresso do upload de vídeo.

    Num terminal, uma linha que se reescreve; fora dele (logs, pipes), uma linha a
    cada 10% — `\\r` num arquivo de log vira lixo.
    """

    def __init__(self, stream: TextIO | None = None, *, tty: bool | None = None) -> None:
        self._stream = stream or sys.stdout
        self._tty = self._stream.isatty() if tty is None else tty
        self._bucket = -1
        self._width = 0

    def __call__(self, event: Event) -> None:
        if event.phase != UPLOADING or event.unit != "bytes" or not event.total:
            self.close()
            return
        pct = event.done * 100 // event.total
        line = f"  {ARROW} enviando {event.done / _MB:.1f}/{event.total / _MB:.1f} MB ({pct}%)"
        if self._tty:
            self._stream.write("\r" + line.ljust(self._width))
            self._stream.flush()
            self._width = len(line)
        elif pct // 10 > self._bucket:
            self._bucket = pct // 10
            self._stream.write(line + "\n")
            self._stream.flush()

    def close(self) -> None:
        """Encerra a linha que se reescreve, antes de qualquer outra saída."""
        if self._width:
            self._stream.write("\n")
            self._stream.flush()
            self._width = 0


class FinishPost:
    """Ctrl+C durante o POST /posts deixaria a dúvida "publicou ou não?".

    Antes do POST, Ctrl+C aborta limpo. Enquanto ele está no ar, o sinal é segurado
    e o comando termina mostrando o URN.
    """

    def __init__(self) -> None:
        self._previous = None

    def arm(self) -> None:
        if self._previous is None and threading.current_thread() is threading.main_thread():
            self._previous = signal.signal(signal.SIGINT, self._hold)

    def disarm(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    @staticmethod
    def _hold(*_: object) -> None:
        err("\n  aguarde: o post já está sendo criado e não dá para cancelar agora")


def _check_media_flags(args: argparse.Namespace) -> None:
    if args.video and args.alt:
        raise InvalidPostError("--alt vale só para --image; no vídeo, use --title")
    if args.title is not None and not args.video:
        raise InvalidPostError("--title vale só para --video")


def cmd_post(args: argparse.Namespace) -> int:
    text = _resolve_text(args)
    _check_media_flags(args)
    alts = _pair_alts(args.image, args.alt)

    # Valida tudo antes de qualquer chamada de rede: um erro de formato não deve
    # ser descoberto depois de já ter subido três imagens.
    draft = prepare(
        text,
        zip(args.image, alts),
        visibility=args.visibility,
        video=args.video,
        video_title=args.title,
    )
    for img in draft.images:
        _out(f"  imagem ok: {img.info.describe()}")
    if draft.video:
        _out(f"  vídeo ok: {draft.video.info.describe()} — título: {draft.video.title}")
    for warning in draft.warnings:
        _out(f"  {WARN} {warning}")

    if args.dry_run:
        _out("\n--- dry-run: nada foi enviado nem publicado ---")
        _out(json.dumps(draft.preview_payload(), indent=2, ensure_ascii=False))
        return 0

    auth = LinkedInAuth.load()
    _out(f"\nAutor: {auth.name} ({auth.person_urn})")

    progress = UploadProgress()
    guard = FinishPost()

    def on_event(event: Event) -> None:
        progress(event)
        if event.phase == PUBLISHING:
            guard.arm()
        else:
            guard.disarm()

    def notify(message: str) -> None:
        progress.close()
        _out(message)

    try:
        urn = publish(auth, draft, notify=notify, on_event=on_event)
    finally:
        progress.close()
        guard.disarm()
    _out(f"\n✓ Post publicado ({draft.visibility})")
    _out(f"  {urn}")
    _out(f"  {post_url(urn)}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "post",
        help="publica um post no seu perfil",
        description="Publica um post de texto, com uma imagem (media), com duas a "
        "vinte imagens (multiImage) ou com um vídeo MP4.",
        epilog="exemplos:\n"
        '  linkedin-bot post "Meu texto" --image foto.jpg\n'
        "  linkedin-bot post --from-file post.txt --image a.jpg --image b.jpg\n"
        '  linkedin-bot post "Demo nova" --video demo.mp4 --title "Demo"\n'
        '  cat post.txt | linkedin-bot post - --image foto.jpg\n'
        '  linkedin-bot post "rascunho" --image foto.jpg --dry-run',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "text",
        nargs="?",
        metavar="TEXTO",
        help="texto do post; use '-' para ler de stdin",
    )
    p.add_argument(
        "--from-file",
        metavar="ARQUIVO",
        help="lê o texto do post de um arquivo",
    )
    media = p.add_mutually_exclusive_group()
    media.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="CAMINHO",
        help="imagem a anexar; repita para até 20 (JPG, PNG ou GIF)",
    )
    media.add_argument(
        "--video",
        metavar="CAMINHO",
        help="vídeo a anexar: um MP4 de 3 s a 30 min, até 500 MB",
    )
    p.add_argument(
        "--title",
        metavar="TEXTO",
        help="título do vídeo (padrão: nome do arquivo sem extensão)",
    )
    p.add_argument(
        "--alt",
        action="append",
        default=[],
        metavar="TEXTO",
        help="texto alternativo; um vale para todas, ou um por --image",
    )
    p.add_argument(
        "--visibility",
        choices=sorted(VISIBILITY),
        default="public",
        help="visibilidade do post (padrão: public)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="valida a mídia e imprime o JSON sem enviar nem publicar",
    )
    p.set_defaults(func=cmd_post)
