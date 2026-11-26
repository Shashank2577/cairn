#!/bin/bash
# Replicates (to measure run-to-run noise) and the combined candidates.   usage: run_matrix2.sh <snap dir> <out dir>
SNAP=$1; OUT=$2; PY=${PY:-python3}; HERE=$(cd "$(dirname "$0")" && pwd)
r() { label=$1; shift; $PY "$HERE/replay.py" --snap "$SNAP" --out "$OUT" --prior --label "$label" "$@" > "$OUT/$label-$(basename "$SNAP").log" 2>&1; }
NC="--env DISABLE_PROMPT_CACHING=1"; T0="--env MAX_THINKING_TOKENS=0"
LEAN="--patch terse --patch trailer_once --patch nocompress"
r nocache_r2 $NC
r nocache_r3 $NC
r think0_r2 $NC $T0
r think0_r3 $NC $T0
r lean $NC $T0 $LEAN
r lean_r2 $NC $T0 $LEAN
r lean2 $NC $T0 $LEAN --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
r lean2_r2 $NC $T0 $LEAN --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
