# MCP tool and HTTP API contract

**MCP**: `cairn_review(target?: "working" | "staged" | branch | pr, base?: ref, budget?: int)` returns a compact pack (<= `context.budget` tokens): header (base, head, mode), findings (severity, path:lines, text, tag, citations), and ids for `cairn_remember` or the triage commands. Read-only; it stores the review.

**HTTP** (project scope, existing auth and roles; read needs `viewer`, triage needs `member`, rules and settings need `admin`):
- `GET /api/p/<id>/reviews?branch=&pr=&limit=` list
- `GET /api/p/<id>/reviews/<rid>` detail with findings, skipped, sent
- `POST /api/p/<id>/reviews` run one `{target, base, model}`
- `POST /api/p/<id>/review-findings/<fid>/accept` | `/dismiss {reason}` | `/rule`
- `GET /api/p/<id>/review-rules` | `POST` | `DELETE /<rid>`
- `GET /api/p/<id>/files/<path>/review-findings` (Impact view)
- The overview payload gains `review: {branch, status, counts, model}`.
