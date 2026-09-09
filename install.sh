#!/usr/bin/env sh
# Instalador do linkedin-bot para macOS e Linux.
# Uso:  ./install.sh [--yes]
set -eu

YES=0
[ "${1:-}" = "--yes" ] && YES=1

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$(printf '\033[1m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m')
  R=$(printf '\033[31m'); D=$(printf '\033[2m'); N=$(printf '\033[0m')
else
  B=''; G=''; Y=''; R=''; D=''; N=''
fi

ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '  %s✗%s %s\n' "$R" "$N" "$1"; exit 1; }
step() { printf '\n%s%s%s\n' "$B" "$1" "$N"; }

REPO=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
LOCAL_BIN="$HOME/.local/bin"

printf '\n%slinkedin-bot — instalação%s\n' "$B" "$N"
printf '%s%s%s\n' "$D" "$REPO" "$N"

# ---------------------------------------------------------------- 1/5
step "[1/5] Verificando o ambiente"

OS=$(uname -s)
case "$OS" in
  Darwin) ok "sistema: macOS ($(uname -m))" ;;
  Linux)  ok "sistema: Linux ($(uname -m))" ;;
  *)      die "sistema não suportado: $OS. No Windows use install.ps1" ;;
esac

[ -f "$REPO/pyproject.toml" ] || die "pyproject.toml não encontrado em $REPO"
ok "projeto encontrado"

if command -v curl >/dev/null 2>&1; then DL=curl
elif command -v wget >/dev/null 2>&1; then DL=wget
else DL=''; fi

if command -v python3 >/dev/null 2>&1; then
  PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo '?')
  ok "python3 presente ($PYV)"
else
  warn "python3 não encontrado — o uv instalará o interpretador"
fi

if command -v linkedin-bot >/dev/null 2>&1; then
  warn "já instalado em $(command -v linkedin-bot) — será atualizado"
fi

# ---------------------------------------------------------------- 2/5
step "[2/5] Instalando o uv"

if command -v uv >/dev/null 2>&1; then
  ok "uv já instalado ($(uv --version 2>/dev/null | cut -d' ' -f2))"
else
  [ -n "$DL" ] || die "curl ou wget necessários para instalar o uv.
     Instale um deles, ou instale o uv manualmente: https://docs.astral.sh/uv/"
  printf '  baixando uv...\n'
  if [ "$DL" = curl ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "falha ao instalar o uv"
  else
    wget -qO- https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "falha ao instalar o uv"
  fi
  PATH="$LOCAL_BIN:$PATH"; export PATH
  command -v uv >/dev/null 2>&1 || die "uv instalado mas não encontrado em $LOCAL_BIN"
  ok "uv instalado"
fi

# ---------------------------------------------------------------- 3/5
step "[3/5] Instalando o linkedin-bot"

uv tool install --editable "$REPO" --force >/dev/null 2>&1 \
  || die "falha na instalação. Rode para ver o erro:
     uv tool install --editable \"$REPO\" --force"
ok "instalado (modo editável: alterações no código valem na hora)"

# ---------------------------------------------------------------- 4/5
step "[4/5] Conferindo o PATH"

case ":$PATH:" in
  *":$LOCAL_BIN:"*) ok "$LOCAL_BIN já está no PATH" ;;
  *)
    warn "$LOCAL_BIN não está no PATH"
    case "${SHELL:-}" in
      */zsh)  PROFILE="$HOME/.zshrc" ;;
      */bash) [ -f "$HOME/.bashrc" ] && PROFILE="$HOME/.bashrc" || PROFILE="$HOME/.bash_profile" ;;
      */fish) PROFILE="$HOME/.config/fish/config.fish" ;;
      *)      PROFILE="$HOME/.profile" ;;
    esac
    LINE="export PATH=\"$LOCAL_BIN:\$PATH\""
    [ "${PROFILE##*/}" = "config.fish" ] && LINE="set -gx PATH $LOCAL_BIN \$PATH"

    ADD=$YES
    if [ "$YES" -eq 0 ] && [ -t 0 ]; then
      printf '    adicionar ao %s? [S/n] ' "$PROFILE"
      read -r ans || ans=n
      case "$ans" in ''|s|S|y|Y) ADD=1 ;; *) ADD=0 ;; esac
    fi

    if [ "$ADD" -eq 1 ]; then
      printf '\n# linkedin-bot\n%s\n' "$LINE" >> "$PROFILE"
      ok "adicionado a $PROFILE (abra um terminal novo, ou: source $PROFILE)"
    else
      warn "adicione manualmente ao $PROFILE:"
      printf '      %s\n' "$LINE"
    fi
    ;;
esac

# ---------------------------------------------------------------- 5/5
step "[5/5] Diagnóstico do ambiente"

BOT="$LOCAL_BIN/linkedin-bot"
[ -x "$BOT" ] || BOT=$(command -v linkedin-bot || echo '')
[ -n "$BOT" ] || die "linkedin-bot não encontrado após a instalação"
"$BOT" doctor || true

printf '\n%sPróximo passo:%s\n' "$B" "$N"
printf '  linkedin-bot auth setup    %s# cadastra o app LinkedIn e autentica%s\n\n' "$D" "$N"
