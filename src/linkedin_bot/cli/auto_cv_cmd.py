"""Subcomandos de `linkedin-bot auto-cv`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def ask_and_confirm(prompt: str) -> str:
    """Faz uma pergunta ao usuário e pede confirmação do valor digitado."""
    while True:
        value = input(prompt).strip()
        confirm = input(f"Você digitou: '{value}'. Confirma? [s/N]: ").strip().lower()
        if confirm in ('s', 'sim', 'y', 'yes'):
            return value
        print("Vamos tentar novamente...\n")


def cmd_auto_cv(args: argparse.Namespace) -> int:
    if args.setup:
        print("Iniciando setup do auto-cv...\n", file=sys.stdout)
        
        full_name = ask_and_confirm("Full Name: ")
        phone_number = ask_and_confirm("Phone Number (com DDD) [ex: (21) 99999-9999]: ")
        linkedin_url = ask_and_confirm("Link do perfil do LinkedIn: ")
        cv_file_input = ask_and_confirm("Arquivo do currículo (.pdf ou .docx) [use '@' antes do caminho]: ")
        keywords = ask_and_confirm("Keywords para a busca (ex: Desenvolvedor de software, remoto): ")
        
        print("\n=== Configuração do Navegador ===")
        print("1) Google Chrome")
        print("2) Brave Browser")
        print("3) Microsoft Edge")
        print("4) Vivaldi")
        while True:
            browser_choice = input("Selecione seu navegador principal (para sincronizar sua sessão) [1-4]: ").strip()
            browser_map = {'1': 'chrome', '2': 'brave', '3': 'edge', '4': 'vivaldi'}
            if browser_choice in browser_map:
                selected_browser = browser_map[browser_choice]
                break
            print("Opção inválida. Tente novamente.")
            
        # Validar Phone Number (se houver letras, quebra e não salva)
        if any(c.isalpha() for c in phone_number):
            print("Erro: O número de telefone não pode conter letras. Setup cancelado.", file=sys.stderr)
            return 1
            
        # Validar arquivo do currículo (suporte ao '@' e ao '~')
        cv_file_raw = cv_file_input.lstrip("@").strip()
        cv_file_path = str(Path(cv_file_raw).expanduser())
        
        if not (cv_file_path.lower().endswith(".pdf") or cv_file_path.lower().endswith(".docx")):
            print("Erro: O currículo deve ser um arquivo .pdf ou .docx. Setup cancelado.", file=sys.stderr)
            return 1

        # Tratar Full Name (First Name e Last Name)
        parts = full_name.split()
        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) >= 2 else ""
            
        config_data = {
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "linkedin_url": linkedin_url,
            "cv_file": cv_file_path,
            "keywords": keywords,
            "browser": selected_browser
        }
        
        # Salvar em json
        config_path = Path("cv_config.json")
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
            
        print(f"\nSetup concluído com sucesso! Configurações salvas em {config_path.name}")
        
    else:
        print("Executando rotina principal do auto-cv...", file=sys.stdout)
        config_path = Path("cv_config.json")
        
        if not config_path.exists():
            print("Erro: Arquivo de configuração (cv_config.json) não encontrado.", file=sys.stderr)
            print("Por favor, rode primeiro: linkedin-bot auto-cv --setup", file=sys.stderr)
            return 1
            
        with config_path.open("r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        from ..cv_automation import run_automation
        run_automation(config_data)
        
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    auto_cv = subparsers.add_parser(
        "auto-cv", 
        help="gerencia a geração de currículo"
    )
    
    auto_cv.add_argument(
        "--setup", 
        action="store_true", 
        help="configura as variáveis iniciais do auto-cv"
    )
    
    auto_cv.set_defaults(func=cmd_auto_cv)
