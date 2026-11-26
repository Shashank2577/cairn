# Research: Cairn Review

Labels: READ = read from a cited source; INFERRED = reasoning to verify.

## D1. Engine: Cairn-native Python pipeline, not the Go binary
- **Decision**: implement the pipeline in Python inside Cairn; keep a compatible `rules.json` shape and a seam for a different stage-2 reviewer.
- **Rationale**: (a) READ (earlier report, `docs/research/2026-10-17-named-repos.md` §3): the tool is a Go CLI needing Git 2.41+ and Node or a release binary, 316 open issues including Windows console and orphaned-process bugs, and releases about daily; (b) READ (`pyproject.toml`): Cairn ships pure Python wheels and its constitution III forbids install steps; a bundled binary means per-platform wheels (macOS/Linux/Windows × architectures); (c) "should not feel like another tool" is easiest when no second process, session store (`~/.open-code-review`) or key exists; (d) INFERRED: its value is the pipeline design, which is small once Cairn's own context (history, specs, conventions) supplies what it lacks.
- **Alternatives**: bundle the binary (L effort, wheels per platform, churn); download on first use (network at runtime, supply-chain pinning, breaks offline); call an installed `ocr` (a user-installed third-party dependency, ruled out by the owner's rule); its delegation mode (still needs the binary).
- **T001 result (2026-10-17)**: this build copies no code, prompt text or rule wording from the design source; only the pipeline shape (bundle, rule match, review, reflect) is followed, and the shared `rules.json` shape is a plain `[{path, rule}]` list. So no `NOTICE` or `licenses/` entry is added. The upstream licence (reported as Apache-2.0 in earlier research) was not re-fetched in this offline pass; re-check before any text or rule wording is ever copied.
- **Open check (T001, original wording)**: confirm the license text and whether any prompt text or rule wording is reused; if so add attribution to `NOTICE` and `licenses/` (Apache-2.0, same as Cairn).

## D2. Deterministic stage (works with no model)
Checks, each producing cited findings: files that usually change together but are missing (co-change table, `cochange`); open drift on touched requirements (`drift.py`); past fixes/reverts on the touched code (history/chronicle); conventions and gotchas linked to touched files (memory); new/changed dependencies without a lock update; secrets in the diff (needs the improved masking patterns); tests likely affected but absent from the diff (`tests_for` in the map). INFERRED: each is a query over data already in the read model.

## D3. Model stage
Bundle changed files by directory/related symbols into bundles under a token cap; match rules per bundle; one `review.bundle` call per bundle (balanced tier) with the cached stable prefix (project brief); a `review.reflect` call (fast tier) drops low-confidence or duplicate findings and checks line numbers against the diff. Budget `review.budget_tokens` (default 60,000) and `review.wall_seconds` (180); when exhausted the review is marked partial. READ (`docs/models-and-cost.md`): the existing `review` task is the frontier, opt-in whole-system review, so new names are used to avoid changing its meaning.

## D4. Storage
`brain.db` tables (data-model.md). READ (memory 38319b18e9): all surfaces read one read model. Reviews are keyed by (project, head commit, base); a re-delivered event finds the existing row.

## D5. Triggers
- Manual: `cairn review`.
- CI: `cairn review --ci --base <ref>` prints machine-readable output (JSON, plus SARIF later) and exits 1 on findings at or above `review.fail_on` (default `high`).
- Server: extend the existing push-webhook route to pull-request open/update events (GitHub `pull_request`, GitLab `merge_request`, Gitea/Bitbucket equivalents) with the same signature checks (READ, `docs/teams.md` §Webhooks).
- Local: opt-in pre-push hook installed by `cairn init`, deterministic only, never blocks, removed by `cairn uninstall`.

## D6. Forge posting and identity
One summary comment per pull request, updated in place by a hidden marker. GitHub first (REST via `httpx`); GitLab and Gitea after. Token per project, stored under the server key, optional; CI mode may use the CI-provided token. The PR author is matched to a Cairn user by forge login (new optional field on the account).

## D7. Agent surface
MCP tool `cairn_review` returning a pack within `context.budget`; added to the core set (13th, ~130 tokens/turn) because Story 6 needs it by default; `/cairn:review` command for Claude Code and equivalents where commands exist. The tool description names only real tools.

## D8. UI (details in quickstart and contracts/mcp-and-api.md)
- New **Review** view in the project nav: list (branch/PR, commit, time, severity counts, tokens, model used, status) → detail (findings by severity, evidence, Accept / Dismiss / Make a rule, "what was sent" panel, skipped files).
- **Impact** view: an "Open review findings" block for the file. **Specs** view: review state per task. **Timeline**: review events. **Overview**: latest review of the current branch with counts. **Settings**: review budgets, fail threshold, forge posting, automatic triggers. **Team → Projects**: per-project automatic review switch and PR review status.

## D9. Rules
Rules come from three sources: stored conventions and gotchas linked to files (generated, not typed), "Make a rule" from a finding, and an imported rules file. Stored in `review_rules` and exported to `.cairn/review/rules.json` as `[{path, rule}]` entries so a team can version them.

## D10. Learning
Three accepts of the same finding signature (rule id + normalized message + path glob) create a memory proposal (kind `gotcha` or `convention`), stored only after the user confirms. Dismissals create suppressions with reason and match counter.
