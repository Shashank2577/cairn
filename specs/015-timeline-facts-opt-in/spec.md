# 015 — Timeline facts are opt-in

## Why
Measured on 2026-11-15 across the 25 repositories on one machine (spec 014): in the last 30 days Cairn's own model
calls cost about $272 at list price, and timeline facts (`temporal.*`) were $147 of it, the largest share. In one
repository they were $74 of $90 over nine days. They ran on every sync, including the background sync after each
commit, whenever a model was available (`[deep] enabled = "auto"`). Constitution II: deterministic first, models second.

## Requirements
- FR-1 A sync builds timeline facts only when `[deep] timeline_facts = true` (default `false`) or when `--deep` is passed.
- FR-2 Existing repositories change too: the key is new, so a config that carries `enabled = "auto"` no longer builds them.
- FR-3 A plain sync with a model available says that timeline facts are opt-in and how to turn them on.
- FR-4 `[deep] enabled = false` still turns every model feature off; `Deep` in doctor/status is unchanged.
- FR-5 The key is settable through the settings API; README, configuration, CLI and cost docs describe it.

## Not changed
Facts already built stay in the store and in context packs. Session notes (`[recall] worker_spawn`) are unchanged.
