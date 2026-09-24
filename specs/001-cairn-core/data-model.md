# Data Model: Cairn read model (`.cairn/brain.db`)

| Table | Key | Fields | Notes |
|---|---|---|---|
| `entities` | `id` (`kind:key`) | kind, name, path, meta JSON, updated_at | kinds: file, symbol, rationale, spec, story, req, task, commit, session, obs, memory, fact |
| `links` | (src, dst, rel) | provenance (EXTRACTED/INFERRED), confidence 0–1, source | rels: owns, implements, touches, reads, modifies, mentions, co_changes, supersedes, part_of |
| `events` | `id` | ts (epoch), kind, title, body, actor, refs JSON, source | kinds: commit, spec, session, memory, fact, drift |
| `memories` | `id` | text, kind, scope, source, provenance, confidence, created_at, superseded_by, engine_ref | kinds: convention, decision, gotcha, preference, fact |
| `cochange` | (a, b) | count, last_ts | file pairs changed in the same commit (≤ 40 files/commit) |
| `kv` | key | value | cursors: last_commit, last_obs_id, map_mtime, spec_hash:* |
| `ledger` | id | ts, task, tier, model, input_tokens, output_tokens, cache_read, cache_write | cost accounting |
| `fts` | FTS5 | id, kind, title, body | search over entities, events, memories |

## Canonical ids
`file:src/pay/service.py` · `symbol:<map node id>` · `spec:001-cairn-core` ·
`req:001-cairn-core/FR-003` · `story:001-cairn-core/US2` · `task:001-cairn-core/T014` ·
`commit:<sha>` · `obs:<id>` · `session:<id>` · `memory:<id>` · `fact:<uuid>`

## Link rules (linker)
| From → To | Rule | Provenance |
|---|---|---|
| symbol → file | map node `source_file` | EXTRACTED |
| task → file | path-like tokens in task text that exist in repo | EXTRACTED |
| task → story / req | `[USn]` tag; `FR-###` mentions | EXTRACTED |
| commit → file | `git log --numstat` | EXTRACTED |
| obs → file | `files_read` / `files_modified` | EXTRACTED |
| memory → symbol/file | word-boundary mention of labels ≥ 4 chars, unique | INFERRED (0.7) |
| file ↔ file | co-change count ≥ 3 and ratio ≥ 0.3 | INFERRED (ratio) |

## State transitions
Memory: `active → superseded` (via `--supersedes`) → `forgotten` (soft delete).
Task: `open → done` from checkbox; drift may flag `done → drifted`.
