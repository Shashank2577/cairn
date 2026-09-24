#!/bin/sh
# Cairn installer: one line, no sudo.  curl -fsSL <raw-url>/install.sh | sh
#   CAIRN_EXTRAS=deep   also install the deep tier (temporal facts, semantic memory, local embeddings)
#   CAIRN_SOURCE=...    install from a different source (default: the published package)
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

say "Installing the spec workflow CLI…"
uv tool install --upgrade specify-cli --from git+https://github.com/github/spec-kit.git >/dev/null 2>&1 \
  || say "Spec workflow CLI skipped (Cairn will fetch it on demand)."

uv tool update-shell >/dev/null 2>&1 || true
say "Done. Open a terminal in any git repository and run:  cairn"
