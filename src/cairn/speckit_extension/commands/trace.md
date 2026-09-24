---
description: "Link tasks to code, flag risky files and co-change gaps"
---

# Trace tasks to code

## Steps

### Step 1: Refresh the memory
Run `cairn sync --no-map-rebuild --quiet` so the new tasks are indexed.

### Step 2: Review risk per task
Run `cairn specs <feature-id> --json`. For every task that names an existing file, run
`cairn impact <file> --budget 400`. Where a file has historical warnings or files that usually
change with it that no task mentions, append a note to that task in `tasks.md`:
`(Cairn: also check <file>; past incident <sha>)`.

### Step 3: Report
Summarise in 5 lines: tasks with HIGH-risk files, missing co-change coverage, and tasks marked
`[P]` that actually touch the same files (not safe to parallelise).
