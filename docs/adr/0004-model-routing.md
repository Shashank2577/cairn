# ADR-0004: Task-tiered model routing

**Status:** Accepted · **Date:** 2026-09-24

## Context
Model work ranges from thousands of tiny classifications to a handful of judgments. One model for
everything is either too expensive or too weak.

## Decision
Four tiers with configurable models: fast (Haiku 4.5), balanced (Sonnet 5), deep (Opus 5.5),
frontier (Fable 5.1, opt-in only). Jobs map to tiers in `router.TASK_TIER`. Automatic escalation
is limited to one step on oversized input and never reaches frontier. Stable prefixes are
prompt-cached. Every call goes to the ledger.

## Consequences
Predictable cost with most calls on the cheaper tiers; teams can pin tiers to other providers
(`provider = "openai"` with a `base_url`) without code changes.
