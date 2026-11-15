# 014 — Net value: Cairn's own spend next to the saving it reports

## Why
`cairn recap` reported "saved ~N tokens (98% vs reading the files)" and never showed what Cairn's own model calls cost.
Read from the stores of the 25 repositories registered on one machine (2026-11-15): 573 packs, at most 16.4M tokens
kept out of agent context (about $61 at each repository's deep-tier input price), against 37,098 model calls of Cairn's
own, 409M tokens, about $512 at list price. By task, lifetime: session observer about $280 (most of it Haiku 4.5 with 1-hour cache
writes, before spec 002's defaults), timeline facts (`temporal.*`) about $174. Last 30 days ($272): timeline facts $147,
observer $105. Thinking is 41% of timeline output tokens. Dollars are list-price equivalents; with the `claude-code`
provider the cost is plan quota, not cash. The saving is priced at each repository's own deep-tier model, so the
total mixes rates.
The receipt also had three wrong lines: "agent sessions" counted observations, "last activity" printed the first
event's date, and the saving was not labelled as an upper bound. The daemon log held 380 "model call not recorded in
the ledger: database is locked" lines, and the per-repository logs another 140, so recorded spend is a floor.

## Requirements
- FR-1 `cairn recap` shows Cairn's own model calls (count, tokens measured, dollars estimated) and the three costliest tasks.
- FR-2 The saving is labelled an upper bound, counts only packs with a file baseline, and is priced at the deep tier's input rate when that rate is known.
- FR-3 A `net` line states which of the two is larger and by how much; no dollar figure is shown when a price is unknown.
- FR-4 `cairn recap --all` prints the same for every repository registered on the machine, biggest spender first, with totals.
- FR-5 A ledger row that cannot be written because the store is locked is kept and written with the next call or on close.
- FR-7 A last-30-days spend line appears beside the lifetime total when they differ.
- FR-6 "agent observations" and "last activity" are correct.

## Out of scope (follow-ups)
- Changing the `[deep] enabled = "auto"` default (timeline facts are the largest spender in the last 30 days).
- A measured saving (A/B) to replace the upper bound.
- The Overview page.
