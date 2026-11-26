#!/bin/bash
# Held-out check: a DIFFERENT 50-event window (seed 11) of the same repository, never used to choose the candidate.
# usage: run_heldout.sh <snap dir> <out dir>
SNAP=$1; OUT=$2; PY=${PY:-python3}; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$OUT"
r() { label=$1; shift; $PY "$HERE/replay.py" --snap "$SNAP" --out "$OUT" --prior --seed 11 --label "$label" "$@" > "$OUT/$label-$(basename "$SNAP").log" 2>&1; }
NC="--env DISABLE_PROMPT_CACHING=1"; T0="--env MAX_THINKING_TOKENS=0"
LEAN="--patch terse --patch trailer_once --patch nocompress"
r baseline
r nocache $NC
r nocache_r2 $NC
r lean2 $NC $T0 $LEAN --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
r lean2_r2 $NC $T0 $LEAN --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
r derive --derive
