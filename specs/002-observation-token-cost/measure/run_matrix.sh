#!/bin/bash
# Runs every variant on the same 50 captured events of one repository snapshot.   usage: run_matrix.sh <snap dir> <out dir>
# Each variant changes ONE thing relative to `nocache` (the first six) so the effect of each lever is attributable.
SNAP=$1; OUT=$2; PY=${PY:-python3}; HERE=$(cd "$(dirname "$0")" && pwd)
r() { label=$1; shift; $PY "$HERE/replay.py" --snap "$SNAP" --out "$OUT" --prior --label "$label" "$@" > "$OUT/$label-$(basename "$SNAP").log" 2>&1; }
NC="--env DISABLE_PROMPT_CACHING=1"
r baseline
r nocache $NC
r think0 $NC --env MAX_THINKING_TOKENS=0
r think1k $NC --env MAX_THINKING_TOKENS=1024
r batch10 $NC --set observe_batch=10
r replay2 $NC --set observer_replay_exchanges=2 --set observer_replay_chars=6000
r diet $NC --set observer_prompt_chars=12000
r terse $NC --patch terse --patch trailer_once
r combo $NC --env MAX_THINKING_TOKENS=1024 --set observer_replay_exchanges=2 --set observer_replay_chars=6000 --set observer_prompt_chars=12000 --patch terse --patch trailer_once
r gemma_ollama --provider openai --base-url http://localhost:11434/v1 --model gemma4:e4b
