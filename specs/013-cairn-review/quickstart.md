# Quickstart: validating Cairn Review

Prerequisites: a repository with Cairn set up, a branch with changes against `main`.

1. **No model** — unset keys and `CAIRN_NO_CLI_MODELS=1`; run `cairn review`. Expect findings with citations and a line saying a model review was not run.
2. **Seeded issues** — in the fixture repo (tests/review/fixtures), run the review; expect the seeded missing co-change, past-fix, convention and secret findings (SC-003).
3. **Model** — configure a provider; run `cairn review --model`; expect model findings marked as written by a model, a ledger entry under `review.bundle`, tokens within `review.budget_tokens`.
4. **Triage** — `cairn review accept <id>`, `dismiss <id> --reason "..."`, `rule <id>`; run again; dismissed pattern is suppressed and counted.
5. **Page** — `cairn ui`, open Review; the review appears; open a finding; Accept; reload; state persists. Open the file's Impact view; the open finding shows.
6. **CI** — `cairn review --ci --base main`; expect JSON and exit status 1 when a high finding exists, 0 otherwise.
7. **Server** — in team mode, send a signed `pull_request` event twice; expect one review; push another commit; the first is marked superseded; a summary comment is created once and then updated (against a stub forge in tests).
8. **Agent** — from an MCP client call `cairn_review`; the reply fits the pack budget; every finding has a citation.
9. **Secrets** — put a synthetic token in the diff; expect a secret finding and no token in any model call or posted comment (SC-008).
10. **Name check** — run the engine-name test over CLI help, API strings and UI strings (SC-007).
