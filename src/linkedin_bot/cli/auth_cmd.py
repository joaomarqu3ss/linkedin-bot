"""Subcomandos de `linkedin-bot auth`."""

from __future__ import annotations

import argparse
import getpass
import sys
import time

from ..auth.flow import perform_login
from ..auth.store import AppCredentials, TokenStore
from ..config import EXPIRY_WARNING_DAYS, redirect_uri, settings
from ..transport.errors import NotConfiguredError

CREATE_PAGE_URL = "https://www.linkedin.com/company/setup/new/"
CREATE_APP_URL = "https://www.linkedin.com/developers/apps/new"


def _out(msg: str = "") -> None:
    print(msg, file=sys.stdout)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _wizard(port: int) -> None:
    uri = redirect_uri(port)
    _out(
        f"""
Configuração do app LinkedIn
────────────────────────────
O bot publica no SEU perfil pessoal, mas para falar com a API ele precisa de um
app registrado — e todo app precisa de uma LinkedIn Page como "publisher". A Page
é só isso: metadado administrativo. Ela não assina nem aparece nos seus posts.

  1. Page
     Se você não tem uma, crie em {CREATE_PAGE_URL}
     Tipo "Autônomo" e tamanho "0-1 funcionários" são aceitos. Você vira super
     admin dela, o que torna o passo 3 um clique seu.

  2. App
     Crie em {CREATE_APP_URL}
     Pede: nome, a Page do passo 1, URL de política de privacidade e um logo.

  3. Verificação
     Na aba Settings do app, clique em Verify, gere a URL e aprove — você é o
     super admin da Page.

  4. Produtos
     Na aba Products, confirme "Share on LinkedIn" e "Sign In with LinkedIn using
     OpenID Connect". Costumam vir habilitados na criação.

  5. Redirect URL
     Na aba Auth, adicione exatamente:
         {uri}

  6. Credenciais
     Ainda na aba Auth, copie o Client ID e o Client Secret.
"""
    )


def ensure_app(
    store: TokenStore,
    client_id: str | None,
    client_secret: str | None,
    *,
    port: int,
    interactive: bool,
) -> AppCredentials:
    """Garante credenciais de app no store, rodando o wizard se necessário."""
    if client_id and client_secret:
        app = AppCredentials(client_id.strip(), client_secret.strip())
        store.save_app(app)
        return app

    existing = store.load().app
    if existing and not interactive:
        return existing

    if not sys.stdin.isatty():
        raise NotConfiguredError()

    _wizard(port)
    cid = input("Client ID: ").strip()
    secret = getpass.getpass("Client Secret (não aparece na tela): ").strip()
    if not cid or not secret:
        raise NotConfiguredError()

    app = AppCredentials(cid, secret)
    store.save_app(app)
    _out(f"\n✓ Credenciais gravadas em {store.path}")
    return app


def _print_status(store: TokenStore) -> int:
    state = store.load()

    if state.app is None:
        _out("Nenhum app configurado.")
        _out("→ rode 'linkedin-bot auth setup'")
        return 1

    _out(f"App          client_id {state.app.client_id}")

    if state.token is None:
        _out("Sessão       não autenticado")
        _out("→ rode 'linkedin-bot auth login'")
        return 1

    t = state.token
    _out(f"Autenticado  {t.name or '(sem nome)'}")
    _out(f"Autor        {t.person_urn}")
    _out(f"Escopos      {', '.join(t.scopes)}")

    if t.dead:
        _out("Token        revogado ou rejeitado pela API (HTTP 401)")
        _out("→ rode 'linkedin-bot auth login'")
        return 1

    if t.seconds_remaining <= 0:
        _out(f"Token        expirado em {t.expires_at:%Y-%m-%d %H:%M UTC}")
        _out("→ rode 'linkedin-bot auth login'")
        return 1

    dias = t.days_remaining
    _out(f"Token        expira em {dias} dia(s) — {t.expires_at:%Y-%m-%d %H:%M UTC}")

    if dias <= EXPIRY_WARNING_DAYS:
        _err(
            f"\n⚠  O token expira em {dias} dia(s). Rode 'linkedin-bot auth login' "
            f"antes disso: depois de expirado, a renovação deixa de ser silenciosa "
            f"e exige login completo no navegador."
        )
    return 0


def _login(store: TokenStore, port: int) -> int:
    token = perform_login(store, port, notify=_out)
    _out(f"\n✓ Autenticado como {token.name or token.person_urn}")
    time.sleep(2)
    _out()
    return _print_status(store)


# --- entrypoints dos subcomandos -----------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    store = TokenStore()
    ensure_app(
        store,
        args.client_id,
        args.client_secret,
        port=args.port,
        interactive=True,
    )
    _out("\nPróximo passo: autenticar.")
    return _login(store, args.port)


def cmd_login(args: argparse.Namespace) -> int:
    store = TokenStore()
    ensure_app(store, None, None, port=args.port, interactive=False)

    state = store.load()
    if state.token and not state.token.is_expired and not args.force:
        _out(
            f"Já autenticado como {state.token.name or state.token.person_urn} "
            f"({state.token.days_remaining} dia(s) restantes)."
        )
        _out("Use '--force' para autenticar novamente.")
        return 0

    return _login(store, args.port)


def cmd_status(args: argparse.Namespace) -> int:
    return _print_status(TokenStore())


def cmd_logout(args: argparse.Namespace) -> int:
    store = TokenStore()
    if args.all:
        store.clear_all()
        _out("Token e credenciais do app removidos.")
    else:
        store.clear_token()
        _out("Token removido. As credenciais do app foram mantidas.")
        _out("→ 'linkedin-bot auth login' para autenticar de novo")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    auth = subparsers.add_parser("auth", help="gerencia a autenticação")
    sub = auth.add_subparsers(dest="auth_command", metavar="<subcomando>")
    auth.set_defaults(func=lambda a: (auth.print_help(), 1)[1])

    def add_port(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--port",
            type=int,
            default=settings.callback_port,
            help=f"porta do callback local (padrão: {settings.callback_port}); "
            "a redirect URI correspondente precisa estar cadastrada no app",
        )

    setup = sub.add_parser("setup", help="configura o app LinkedIn e autentica")
    setup.add_argument("--client-id", help="pula o prompt do Client ID")
    setup.add_argument("--client-secret", help="pula o prompt do Client Secret")
    add_port(setup)
    setup.set_defaults(func=cmd_setup)

    login = sub.add_parser("login", help="autentica no LinkedIn pelo navegador")
    login.add_argument(
        "--force", action="store_true", help="reautentica mesmo com token válido"
    )
    add_port(login)
    login.set_defaults(func=cmd_login)

    status = sub.add_parser("status", help="mostra a sessão atual e a validade do token")
    status.set_defaults(func=cmd_status)

    logout = sub.add_parser("logout", help="remove o token armazenado")
    logout.add_argument(
        "--all", action="store_true", help="remove também as credenciais do app"
    )
    logout.set_defaults(func=cmd_logout)
