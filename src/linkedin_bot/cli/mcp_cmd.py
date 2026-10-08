"""Subcomando `linkedin-bot mcp`: servidor MCP em stdio, iniciado pelo cliente de IA."""

from __future__ import annotations

import argparse
import sys


def cmd_mcp(args: argparse.Namespace) -> int:
    if sys.stdin.isatty():
        print(
            "linkedin-bot mcp é o servidor MCP: o cliente de IA o inicia via stdio.",
            file=sys.stderr,
        )
        print("  → para registrar nos agentes: linkedin-bot --ai list", file=sys.stderr)
        return 1

    from ..mcp_server.server import run

    return run()


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "mcp",
        help="inicia o servidor MCP (stdio); é executado pelo cliente de IA",
    )
    p.set_defaults(func=cmd_mcp)
