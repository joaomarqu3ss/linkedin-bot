"""Subcomando `linkedin-bot post`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..auth.session import LinkedInAuth
from ..media.images import inspect_image, upload_image
from ..posts.create import VISIBILITY, build_payload, create_post, post_url


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

    text = text.strip()
    if not text:
        raise SystemExit("erro: o texto do post está vazio")
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


def cmd_post(args: argparse.Namespace) -> int:
    text = _resolve_text(args)
    visibility = VISIBILITY[args.visibility]
    alts = _pair_alts(args.image, args.alt)

    # Valida tudo antes de qualquer chamada de rede: um erro de formato não deve
    # ser descoberto depois de já ter subido três imagens.
    infos = [inspect_image(p) for p in args.image]
    for info in infos:
        _out(f"  imagem ok: {info.describe()}")

    if args.dry_run:
        payload = build_payload(
            "urn:li:person:<AUTOR>",
            text,
            visibility=visibility,
            images=[(f"urn:li:image:<UPLOAD_{i + 1}>", a) for i, a in enumerate(alts)],
        )
        _out("\n--- dry-run: nada foi enviado nem publicado ---")
        _out(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    auth = LinkedInAuth.load()
    _out(f"\nAutor: {auth.name} ({auth.person_urn})")

    uploaded: list[tuple[str, str | None]] = []
    for info, alt in zip(infos, alts):
        uploaded.append((upload_image(auth, info, notify=_out), alt))

    payload = build_payload(
        auth.person_urn, text, visibility=visibility, images=uploaded
    )

    _out("\n→ publicando...")
    urn = create_post(auth, payload)
    _out(f"\n✓ Post publicado ({visibility})")
    _out(f"  {urn}")
    _out(f"  {post_url(urn)}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "post",
        help="publica um post no seu perfil",
        description="Publica um post de texto, com uma imagem (media) ou com duas a "
        "vinte imagens (multiImage).",
        epilog="exemplos:\n"
        '  linkedin-bot post "Meu texto" --image foto.jpg\n'
        "  linkedin-bot post --from-file post.txt --image a.jpg --image b.jpg\n"
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
    p.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="CAMINHO",
        help="imagem a anexar; repita para até 20 (JPG, PNG ou GIF)",
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
        help="valida as imagens e imprime o JSON sem enviar nem publicar",
    )
    p.set_defaults(func=cmd_post)
