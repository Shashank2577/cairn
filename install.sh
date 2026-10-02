#!/bin/sh
# Cairn installer: one line, no sudo.  curl -fsSL <raw-url>/install.sh | sh
#   CAIRN_EXTRAS=memory-server,temporal-neo4j   optional extras (shared stores for team servers, more languages …)
#   CAIRN_SOURCE=...                             install from a different source (default: this repository on GitHub — PyPI publishing is pending)
set -eu
say() { printf '\033[38;5;179m▲\033[0m %s\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python toolchain manager)…"
  # Download to a file first: if curl fails, piping straight into sh runs an empty script and still exits 0.
  uv_install="$(mktemp)"
  trap 'rm -f "$uv_install"' EXIT
  curl -LsSf https://astral.sh/uv/install.sh -o "$uv_install" || {
    say "Could not download the uv installer. Check your network and re-run." >&2
    exit 1
  }
  sh "$uv_install"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || {
    say "uv was installed, but it is not on PATH. Add \"$HOME/.local/bin\" to your PATH and re-run." >&2
    exit 1
  }
fi

SRC="${CAIRN_SOURCE:-cairn-brain}"
[ -n "${CAIRN_EXTRAS:-}" ] && SRC="${SRC}[${CAIRN_EXTRAS}]"
say "Installing Cairn…"
# --python 3.12: kuzu (the timeline store) has no 3.14 wheels on macOS and Windows, so uv must not pick a newer interpreter.
uv tool install --upgrade --python 3.12 "$SRC"

uv tool update-shell >/dev/null 2>&1 || true
if command -v cairn >/dev/null 2>&1; then
  say "Cairn installed at $(command -v cairn)."
else
  # The uv tools bin dir may not be on this shell's PATH yet; look there before failing.
  BIN_DIR="$(uv tool dir --bin 2>/dev/null)" || BIN_DIR="$HOME/.local/bin"
  if [ -x "$BIN_DIR/cairn" ]; then
    say "Cairn installed at $BIN_DIR/cairn."
  else
    say "Cairn was installed, but 'cairn' is not on PATH. Open a new terminal, or add $BIN_DIR to your PATH." >&2
    exit 1
  fi
fi
say "Done. Open a terminal in any git repository and run:  cairn"
