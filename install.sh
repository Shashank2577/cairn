#!/bin/sh
# Cairn installer: one line, no sudo.  curl -fsSL <raw-url>/install.sh | sh
#   CAIRN_EXTRAS=memory-server,temporal-neo4j   optional extras (shared stores for team servers, more languages …)
#   CAIRN_SOURCE=...                             install from a different source (default: the published package)
set -eu
say() { printf '\033[38;5;179m▲\033[0m %s\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python toolchain manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

SRC="${CAIRN_SOURCE:-cairn-brain}"
[ -n "${CAIRN_EXTRAS:-}" ] && SRC="${SRC}[${CAIRN_EXTRAS}]"
say "Installing Cairn…"
uv tool install --upgrade --python 3.12 "$SRC"

uv tool update-shell >/dev/null 2>&1 || true
say "Done. Open a terminal in any git repository and run:  cairn"
