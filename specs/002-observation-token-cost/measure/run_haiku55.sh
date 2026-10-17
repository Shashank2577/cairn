#!/bin/bash
# The same windows on Haiku 5.5 (the CLI's own cost report gives $0.10 in / $0.50 out per MTok for it). usage: run_haiku55.sh <snap> <out> [seed]
SNAP=$1; OUT=$2; SEED=${3:-7}; PY=${PY:-python3}; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$OUT"
r() { label=$1; shift; $PY "$HERE/replay.py" --snap "$SNAP" --out "$OUT" --prior --seed $SEED --model claude-haiku-5-5 --label "$label" "$@" > "$OUT/$label-$(basename "$SNAP").log" 2>&1; }
NC="--env DISABLE_PROMPT_CACHING=1"; T0="--env MAX_THINKING_TOKENS=0"; LEAN="--patch terse --patch trailer_once --patch nocompress"
r h55_baseline
r h55_nocache $NC
r h55_lean $NC $T0 $LEAN
r h55_lean2 $NC $T0 $LEAN --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
