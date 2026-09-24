---
description: "Gather impact, rationale and conventions for the code a feature will touch"
tools:
  - 'cairn/cairn_context'
  - 'cairn/cairn_impact'
---

# Ground the plan in the project's memory

## User Input

$ARGUMENTS

## Steps

### Step 1: Find the code this feature touches
Read the current feature's `spec.md`. Call `cairn_context` with a one-line summary of the feature.
If the MCP tool is unavailable, run `cairn ask "<summary>" --no-llm` in the terminal instead.

### Step 2: Check each likely change point
For the 1–4 most relevant targets, call `cairn_impact`. Note HIGH risk items, historical warnings,
co-changing files and conventions.

### Step 3: Feed the plan
Add a section **"Existing system constraints (from Cairn)"** to `plan.md` (create it if the plan
has not been written yet, and keep it when writing the plan) listing: touched hubs, dependents and
tests to keep green, past incidents to avoid repeating, and conventions to follow — with [ids].
