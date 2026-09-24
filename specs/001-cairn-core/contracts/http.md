# HTTP contract (daemon, `127.0.0.1:4747`)

| Method | Path | Body/Query | Response |
|---|---|---|---|
| GET | `/api/overview` | — | project, layer counts & health, hubs, drift count, last sync |
| GET | `/api/map` | `area?`, `limit?` | `{nodes, links}` — communities by default, members when `area` given |
| GET | `/api/entity` | `id` | entity + links + dossier sections |
| GET | `/api/search` | `q`, `kinds?` | ranked entities |
| GET | `/api/impact` / `/api/why` | `target`, `budget?` | structured sections + markdown |
| GET | `/api/specs` | `id?` | features with stories, requirements, tasks, progress |
| GET | `/api/drift` | `spec?` | findings |
| GET/POST/DELETE | `/api/memories` | query / `{text,kind,supersedes}` / `id` | memories |
| GET | `/api/timeline` | `since?`, `target?`, `limit?` | events |
| GET | `/api/sessions` | `q?` | sessions + observations |
| POST | `/api/ask` | `{question, budget, llm}` | `{pack, answer?}` |
| POST | `/api/sync` | `{deep?}` | `{started: true}`; progress on `GET /api/stream` (SSE) |
| GET | `/api/models` | — | routing table + ledger totals |
