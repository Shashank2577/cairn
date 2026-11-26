# context-mode, ai-memory and billion-context against Cairn

Date: 2026-10-20. Read-only study. Repos were shallow-cloned into a scratchpad outside this checkout; nothing in the repository was run, installed or edited.

Labels: **SRC** = read in the project's source or docs on 2026-10-20. **CLAIM** = stated in a README/doc/website and not verified (no benchmark was re-run). **INF** = my inference.

Earlier reports in this folder (open-code-review, buildpartner, SkillOpt, missing-oss, prompt-coaching) are not repeated.

## 1. What these are

| | context-mode (mksglu) | ai-memory (akitaonrails) | billion-context (ranxianglei), added by request |
|---|---|---|---|
| Category | **Output firewall + session continuity.** Keeps tool output out of the context window. | **Cross-agent long-term memory server.** | **API proxy that compresses the conversation itself.** |
| Stars / age (SRC, `gh api`) | 31.3k, created 2026-02-23 | 11.8k, created 2026-05-21 | 2.1k, created 2026-08-06 |
| Language / license | TypeScript, **Elastic License 2.0** (`NOASSERTION` on GitHub) | Rust, MIT | TypeScript, MIT plus a mandatory attribution clause on product surfaces |

## 2. Side-by-side

| Axis | context-mode | ai-memory | billion-context | Cairn today |
|---|---|---|---|---|
| **Capture** | SRC: hooks on PreToolUse, PostToolUse, UserPromptSubmit, PreCompact, SessionStart, Stop. Deterministic regex extraction into ~15 event categories with priorities (files, tasks, plans, rules, decisions/corrections, git, errors, error→fix pairs, constraints, blockers, **rejected approaches**, env, subagent findings, retry loops). `hooks/`, `src/session/extract.ts` | SRC/CLAIM: lifecycle hooks for 20+ harnesses; sanitised at a typed privacy boundary; server consolidates into markdown pages. Zero LLM by default (CLAIM, matches docs) | SRC: sits in the request path, so it sees every message. Sessions keyed by client conversation id or prefix match | SRC: hooks `UserPromptSubmit`, `PostToolUse`, `Stop`, `PreToolUse(Read)`, `SessionStart` write `.cairn/sessions.db`; deterministic observations without a model (`engines/recall/hooks.py`, `fallback.py`). **No PreCompact hook** |
| **Storage** | Per-session SQLite + FTS5 (BM25, Porter and trigram tables). **Previous session data deleted unless `--continue`** (CLAIM, README) | Git-backed markdown wiki is the source of truth; SQLite is a rebuildable index | Layered, reversible summaries under `~/.local/share/billion-context/` | One SQLite read model (`brain.db`) fed by five engines (map, specs, timeline, memory, sessions); sessions in `sessions.db` |
| **Retrieval** | FTS5 BM25 via `ctx_search`; model asks. Agent is told to search before asking the user | FTS5 by default; optional local embeddings and reranker. Self-reported LongMemEval-S hit@5 0.951 FTS (CLAIM, own harness; v2.6 was 0.666, so the number moved a lot between releases) | Model calls injected tools `compress`, `decompress`, `search_context` | Hybrid search over all layers, `cairn_context` / `cairn_impact` / `cairn_why` with a token budget (default 1800) |
| **Injection** | SessionStart routing block (rules telling the agent to prefer sandbox tools); after compaction a **<2 KB priority-sorted XML resume snapshot** that is a table of contents pointing at searchable events, not the content (SRC: `hooks/precompact.mjs`, `src/session/snapshot.ts`) | Opt-in SessionStart brief, default 4,000 chars (clamp 1.5k–20k), greedy packing of pinned/rules pages; a tiered L0/L1/L2 design is in draft (SRC: `docs/design-tiered-session-brief.md`) | Rewrites the request/response stream; older turns replaced by summaries, restorable | SessionStart brief plus a per-prompt "ambient" nugget (`hooks.py`) and file-context before Read |
| **Token cost** | Savings claim: 315 KB → 5.4 KB (98%) on 14 fixture scenarios, 96% overall incl. retrieval (CLAIM; BENCHMARK.md fixtures are real tool outputs but the baseline is "agent reads everything raw", an upper bound). Statusline shows "$ saved" | Zero model tokens on the default path; brief is a fixed 4k-char cap | CLAIM: "5× fewer tokens", 95–97% prefix-cache hit, ≤2% compression overhead, from a self-reported 4.5-month, 174k-call production study | Context packs are budget-capped and logged with sent tokens vs the size of the files behind them. Briefings count as cost, not savings (SRC: `docs/models-and-cost.md`) |
| **Agents** | Claude Code, Gemini CLI, Codex, Cursor, Copilot, OpenCode, Kiro, Kimi, Qwen, Zed, JetBrains and more (SRC: `src/adapters/` has 17 dirs) | 20+ harnesses (CLAIM) | ~20 via launcher (`bili <agent>`), plugin, or base-URL change | Claude Code, Codex, Cursor, Gemini, OpenCode, Copilot, VS Code (SRC: `docs/agents.md`) |
| **Install** | `/plugin marketplace add` + `/plugin install`, or `npm i -g`; Node ≥ 22.5; per-agent hand edits for some (SRC README) | `curl … \| sh` then `ai-memory setup` (checksum verified, wires agents). Needs a running per-user service | `npm i -g`, then you must launch agents through `bili` or change a base URL: **you change how you run your agent** | `uv tool install` then `cairn`; zero questions; no daemon needed beyond the local server |
| **Dependencies on a model** | None for the core | None by default; LLM "dream" consolidation is opt-in | Yes, summaries are model-written (INF) | None for the core; model optional |

## 3. What each does better than Cairn

**context-mode**

1. **It attacks a cost Cairn does not touch: raw tool output.** Cairn's savings are in what it hands the agent. context-mode intercepts Bash/Read/WebFetch/Grep results and keeps the bulk out of the window (SRC: `hooks/pretooluse.mjs`, `routing-block.mjs`, `src/server.ts`). A 56 KB Playwright snapshot or a 45 KB log is the typical flood. CLAIM on the size of the win, but the mechanism is real and the problem is real.
2. **PreCompact hook plus a resume snapshot** (SRC). When the host compacts, the agent loses the files it was editing and its open tasks. context-mode rebuilds a <2 KB reference-style snapshot from its captured events. Cairn has no PreCompact handling (SRC: grep of `hooks.py`, `engines/recall/hooks.py`, `agents.py` finds none).
3. **Richer deterministic event taxonomy.** Rejected approaches (a tool call the user denied), error→fix pairs, retry loops, blockers, user corrections. Cairn's earlier prompt-coaching report noted the same missing error→fix pairing.
4. **A live savings readout** (statusline `$ saved`) that updates in the agent's own UI. Cairn shows savings on the Overview page only.
5. **Session-aware search with `sort: timeline` and source labels** ("what did we reject", "what constraints exist").

**ai-memory**

1. **Memory aging with no model.** Access-weighted decay, extractive compaction of cold notes to durable facts (paths, error codes, decisions), near-duplicate collapse, contradiction flagging; nothing hard-deleted, `restore-page` reverses (CLAIM from docs; design in `docs/design-memory-aging.md`; off by default for the destructive parts). Cairn memory has supersede and forget (SRC: `engines/memory.py`) but I found no usage-based decay, so stale memories compete forever (INF; I did not audit the whole engine).
2. **Typed, claim-once handoffs between agents.** Quit Claude, start Codex, pick up "where I left off, what failed, what is open" with an owner and exactly-once claim (SRC: `docs/design-handoff-offer-not-claim.md`). Cairn has cross-agent session recall, but not an explicit handoff object.
3. **Tiered L0/L1/L2 brief** so one long rules page cannot eat the budget (SRC design doc, not yet shipped). Cairn's brief packs by section with best-first (SRC: `budgets.py` wording), which is already similar; the gain is smaller than it looks.
4. **Honest benchmarking culture**: in-repo LongMemEval harness, and a comparison table that flags non-comparable numbers (SRC `docs/comparison.md`). Cairn has `evals/`-style work only for observation cost.
5. **Markdown as source of truth.** Humans can grep and edit memory. Cairn's memory lives in SQLite (brain.db); I did not check whether there is a markdown export.

**billion-context**

1. **Solves long-session context rot directly, by compressing in the request path.** Reversible, incremental, prefix-cache-aware, with a cache-hit report that attributes misses (TTL expiry, model switch). That cache attribution is a good idea and Cairn already holds a related gotcha (the `claude -p` 1-hour cache write that is never read; memory 8f139c6bb4).
2. **Cost:** it needs a proxy, a launcher change, and model-written summaries. That violates "one command, zero friction" and "deterministic first". Its own README calls the project early (WebFetch of reporadar page; claim). Its attribution clause is a product-surface requirement that conflicts with Cairn's "engines stay invisible" convention (memory 5b6e2b81b5).

## 4. What Cairn does that none of them do

- **The map.** Tree-sitter graph, hubs, impact and co-change. None of the three knows what depends on a file.
- **Specs and drift.** Spec-to-code tracing and `cairn drift`; nothing comparable in any of them.
- **Timeline of git history** (fixes, reverts, validity-windowed facts). ai-memory has no temporal graph by its own admission (comparison.md).
- **Counterfactual savings measured against the files behind each answer**, with a model-cost ledger and per-tier routing. context-mode measures bytes kept out of context; it does not route work to cheaper models.
- **Team server** with roles, tokens, webhooks (ai-memory also has multi-user auth).
- **One deterministic day-one product**: impact/why answers need no model and no daemon of the user's choosing.

## 5. Constitution check

Principles read from the Cairn brief: deterministic first (II, non-negotiable), zero friction (III), token discipline (V), right model for the job (VI), ships to all users and never depends on personal tools.

| Candidate | II deterministic | III zero friction | V token discipline | Verdict |
|---|---|---|---|---|
| context-mode ideas (events, snapshot, output firewall) | yes, regex and SQLite | PreCompact is free; blocking tools is **not** (see risk) | strong | Borrow selectively |
| ai-memory aging, handoff | yes by design | yes | neutral | Borrow |
| billion-context proxy | no (LLM summaries) | no (changes how you launch agents) | strong | Ignore |
| Copying code | context-mode is **ELv2**: do not copy or ship its code in an Apache-2.0 package. Re-implement from the idea only. ai-memory is MIT but Rust, so reuse would be ideas only anyway. billion-context MIT plus attribution clause: avoid | | | |

## 6. Ranked shortlist

| # | Adopt | Effort | Benefit | Risk | Cairn files | Evidence |
|---|---|---|---|---|---|---|
| 1 | **PreCompact hook + compact resume snapshot.** Build a <2 KB brief from `sessions.db` (files changed, open tasks, last prompt, decisions) and inject it after compaction as pointers to `cairn_session_*` tools. | S–M | Fixes the largest real gap: post-compact amnesia. Deterministic, no model. | Hook support differs per agent (Claude Code has PreCompact; most others do not); must not duplicate the SessionStart brief | `src/cairn/engines/recall/hooks.py`, `src/cairn/agents.py` (wiring), `src/cairn/hooks.py` | Mechanism SRC; benefit INF |
| 2 | **Richer deterministic events:** rejected approaches (denied tool call), error→fix pairs, retry loops, user corrections. Surface them as observation types and as searchable sources. | M | Higher-value recall ("what NOT to repeat") at no model cost; builds on the prompt-coaching report's finding | Regex extraction is noisy; needs fixtures and false-positive review | `src/cairn/engines/recall/fallback.py`, `parser.py`, `modes/` | Taxonomy SRC; value INF |
| 3 | **Measure tool-output bloat first, don't block anything yet.** Add a PostToolUse measurement of tool-result size per tool and show "bytes the agent pulled raw" next to Cairn's savings. Decide on a firewall only after the data shows a large, common cost. | S | Tells you whether context-mode's whole premise applies to Cairn users; cheap | None; read-only | `engines/recall/hooks.py` (observation), `src/cairn/budgets.py`, UI Overview | The 98% figure is CLAIM; this step verifies it on real Cairn sessions |
| 4 | **Zero-model memory aging:** record last-used time when a memory is returned by `cairn_context`/`recall`, down-rank unused ones, never delete, show "stale" in the UI. | M | Keeps injected memory relevant as it grows; matches token discipline | Down-ranking a still-needed convention; keep reversible and off for pinned kinds | `src/cairn/engines/memory.py`, `store.py` (schema), `router.py` | ai-memory design SRC; no effect measured by them yet (they note no eval has shown a gain) |
| 5 | **Statusline/agent-side savings line** ("saved N tokens this session") using data Cairn already logs. | S | Makes value visible where the user looks; fits the "UI must show value" note | Minimal | existing status line in `agents.py`, `queries` ledger | context-mode SRC for the idea |
| 6 | **Explicit handoff object** between agents (what failed, what is open), generated deterministically at Stop. | M | Smoother Claude→Codex switch | Overlaps session recall; only worth it if users switch agents mid-task (unknown) | `engines/recall`, `agents.py` | ai-memory SRC |
| 7 | **Cache-hit awareness** in the ledger (attribute misses to TTL expiry or model switch). | M | Cheap-to-find cost leaks; Cairn already found one | Depends on provider usage fields being available | `router.py`, models ledger | billion-context CLAIM |

Not recommended: an MCP output sandbox that blocks Bash/Read/WebFetch (context-mode's core). It changes agent behaviour, fights host permission models, and context-mode itself needs a long routing block and a stream of compatibility fixes to hold it up (INF from its hook directory size and `CLAUDE.md`). Revisit only if item 3 shows a large cost. Also not recommended: the compression proxy, the "dream" LLM consolidation, vector-store changes, or vendoring any code.

## 7. Uncertainty

- I did not run any of the three tools, so every benchmark and percentage above is a CLAIM. ai-memory's own history (hit@5 0.666 → 0.951 between versions) is a reminder that these numbers swing with harness changes.
- billion-context was reviewed through its reporadar page, its LICENSE and README headings and a file listing, not its core source; treat §3 on it as shallower than for the other two.
- I did not audit Cairn's memory engine end to end, so "no usage-based decay" is an inference from a grep of `engines/memory.py`.
- Cairn's constitution file was not at `.specify/memory/constitution.md` (it lives under `.cairn/workflow/memory/`); I used the principle list from the session brief rather than re-reading it.
- Star counts are as of 2026-10-20 from `gh api`.

## 8. Recommendation

**Borrow ideas; do not adopt any of the three as a dependency.** Start with #1 (PreCompact snapshot) and #3 (measure tool-output bloat), both small and deterministic. Follow with #2 and #4. Ignore billion-context. Do not copy context-mode code (ELv2).
