# ADR-0002: Deterministic first, models second

**Status:** Accepted · **Date:** 2026-09-25

## Context
Model calls cost money or plan quota, add latency and can be wrong. The questions developers and agents ask
most (what depends on this, what changed together, which task owns it, what did the last session touch) have
exact answers in code, git, specs and captured sessions.

## Decision
The code map, history, spec parsing, drift checks, impact, why, context packs and session capture are
computed without a model. Models add what cannot be computed: observations and session summaries written from
raw agent activity, timeline facts with validity windows, memory reconciliation, semantic drift, narrated
answers and document extraction. Every model feature degrades to a deterministic result or a clear
"needs a model" state.

## Consequences
- Cairn is useful on day one with no key and no sign-in.
- Model features switch on automatically when a model is reachable (see ADR-0004).
