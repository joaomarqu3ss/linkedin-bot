from __future__ import annotations

import sys

from linkedin_bot.cli.__main__ import main


def test_terminal_interativo_imprime_dica_e_sai_com_1(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert main(["mcp"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "linkedin-bot mcp é o servidor MCP" in captured.err
    assert "linkedin-bot --ai list" in captured.err


def test_sem_tty_chama_run(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("linkedin_bot.mcp_server.server.run", lambda: 0)
    assert main(["mcp"]) == 0
