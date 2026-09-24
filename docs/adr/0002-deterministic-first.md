# ADR-0002: Deterministic first, models second

**Status:** Accepted · **Date:** 2026-09-24

## Context
Model calls cost money, add latency, need keys and can hallucinate. The core questions (what
depends on this, what changed with it, who owns it, why) have factual answers in ASTs, git and specs.

## Decision
Impact, why, context, drift and briefings are computed without models. Models only (a) narrate
a pack that has already been assembled, (b) build the temporal fact graph, (c) update semantic
memory, and (d) judge a bounded set of requirements for semantic drift. Every item carries
`EXTRACTED`/`INFERRED` provenance and a citation.

## Consequences
Works with zero keys; results are testable and reproducible; model output is always grounded in
cited evidence. Some nuance (for example "is this requirement still honoured?") needs the deep tier.
