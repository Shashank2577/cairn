---
description: "Check the implementation against the spec and record what was learned"
tools:
  - 'cairn/cairn_specs'
  - 'cairn/cairn_remember'
---

# Verify against the spec

## Steps

### Step 1: Sync and check drift
Run `cairn sync --quiet` then `cairn drift <feature-id>` (add `--deep` when a model key is set).

### Step 2: Resolve findings
For each high finding: fix it, or explain why it is a false positive. Re-run until clean.

### Step 3: Capture learnings
Record 1–3 durable learnings from this implementation with `cairn remember ... --kind gotcha|convention|decision`.
Only things a future developer or agent would otherwise get wrong.
