#!/bin/bash
# The AFTER numbers: the shipped defaults (no harness patches). safe = defaults; econ = batch 10 + replay 2/6000; safe55 = defaults on Haiku 5.5.
# usage: run_after.sh <snap dir> <out dir>
SNAP=$1; OUT=$2; PY=${PY:-python3}; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$OUT"
H45=claude-haiku-4-5-20251001
r() { win=$1; seed=$2; label=$3; shift 3; $PY "$HERE/replay.py" --snap "$SNAP" --out "$OUT/$win" --prior --seed $seed --label "$label" "$@" > "$OUT/$label-$win-$(basename "$SNAP").log" 2>&1; }
for w in "main 7" "held 11"; do set -- $w
  r $1 $2 after_safe --model $H45
  r $1 $2 after_econ --model $H45 --set observe_batch=10 --set observer_replay_exchanges=2 --set observer_replay_chars=6000
  r $1 $2 after_safe55 --model claude-haiku-5-5
done
