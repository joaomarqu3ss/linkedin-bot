"""Ponto de entrada da CLI."""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..transport.errors import LinkedInBotError
from ..transport.redact import install as install_redaction
from . import ai_cmd, auth_cmd, doctor_cmd, post_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-bot",
        description="Automação de publicações no LinkedIn.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--ai",
        metavar="ALVO",
        help="instala a skill de integração para um agente de IA "
        "(claude, codex, gemini, antigravity, cursor, agents); "
        "use 'list' para ver os alvos e 'all' para todos os detectados",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<comando>")
    auth_cmd.register(subparsers)
    post_cmd.register(subparsers)
    doctor_cmd.register(subparsers)

    parser.set_defaults(func=lambda a: (parser.print_help(), 1)[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    install_redaction()
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "ai", None):
        return ai_cmd.run(args.ai)

    try:
        return args.func(args)
    except LinkedInBotError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
