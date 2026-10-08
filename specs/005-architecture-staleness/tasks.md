---
description: "Tasks for 005-architecture-staleness (ids kept from the 002 umbrella)"
---

# Tasks: Diagrams that stay true: staleness re-check and drift

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [ ] T027 [US3] Incremental re-derivation for claims whose evidence files changed; stale marking with reason and since in `src/cairn/system/recheck.py` — open
- [ ] T028 [US3] `stale-architecture` drift findings in `src/cairn/drift.py` — open
- [ ] T029 [US3] Tests: remove evidence, then stale; restore, then cleared; import-without-use creates nothing, in `tests/system/test_recheck.py` — open
