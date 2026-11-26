# Feature Specification: Savings Ledger — what Cairn saves, measured honestly

**Feature Branch**: `009-savings-ledger`
**Created**: 2026-10-16
**Status**: Draft (clarified; open questions in [open-questions.md](open-questions.md))
**Input**: User description: "Show, as a product feature, how much AI cost Cairn saves. Every Cairn call an
agent makes (MCP tools and CLI) is recorded with its output size in tokens. Cairn reports an honest
estimate of tokens saved versus the agent reading the same files directly, per call, per session and in
total, in the CLI and in the Map UI. The baseline is defined and labelled estimated; Cairn never claims a
saving the measurement cannot support; the estimate is validated against real agent token usage."

> Numbering: `002`–`008` are taken on the open PR (`002-system-model` and its slices). This is `009` so the
> two branches never collide when merged.

## Background — what already exists, and why it is not yet defensible

Cairn already has a savings meter (`001` User Story 12, FR-020). It is a good start and is what this
feature replaces with something that survives scrutiny. Audit of the code and of a real store
(`.cairn/brain.db` of this repository, 30 rows) on 2026-10-16:

| # | Finding | Where | Consequence |
|---|---|---|---|
| A1 | Only three pack tools log a row with a baseline: `impact`, `why`, `context` (plus `ask`, `brief`, `recall`). `cairn_search`, `cairn_trace`, `cairn_specs`, `cairn_facts`, `cairn_recall`, `cairn_remember`, `cairn_status`, the session tools and every generated graph/timeline tool log nothing. | `core.py` `_logger`, `mcp_server.py` | "Every call recorded" is false today; cost is under-counted. |
| A2 | The baseline is the whole size of every file cited, in bytes ÷ 4, with no cap. One real `cairn_impact` row claims 144,228 source tokens for 33 files; a CLI row claims 260,555 for 223. | `core.py` `source_cost` | No agent reads 33–223 whole files to answer one question. The headline "saved" is an upper bound presented as a measurement. |
| A3 | Every call re-credits the same files. Six CLI `impact` rows cite overlapping files and each is credited in full. | `queries` table | An agent that read a file once would hold it for the rest of the session; repeat credit double counts. |
| A4 | `cairn_context` and `ask` appear to log twice: the pack logs on first render (with a baseline) and the tool then logs again (without one). Verify with a test. | `mcp_server.py:105`, `core.py` `ask` | Cost and call counts can be inflated; ratios skewed. |
| A5 | Cairn's own overhead is not netted out except session briefings: MCP tool schemas (paid every turn), model calls (`ledger` table) and narration are excluded. | `overview.js` `Served` | Gross presented as net. |
| A6 | `cairn recap` computes its own saved figure (`src − sent` over all rows, including rows with no baseline) and so disagrees with the Overview page. | `hooks.py` `recap` | Two numbers for one thing. |
| A7 | `cairn graph benchmark` compares to a whole-corpus read, and when no word count is given it *invents* the corpus as `nodes × 50` words. | `engines/graph/benchmark.py` | Not a baseline anyone could defend; must not feed the ledger. |
| A8 | The graph engine keeps a separate JSONL query log (`graph-queries.log`) with response sizes in characters. | `engines/graph/querylog.py` | Third place a call may be (not) recorded. |
| A9 | `estimate_tokens` is `len(text)//4` everywhere, undeclared as a method, never checked against a real tokenizer. | `router.py` | The unit of every number is unstated and unvalidated. |
| A10 | Nothing groups calls into agent sessions, so "per session" cannot be answered. | `queries` table | Missing requirement. |

## Principles this feature must keep

- **Honest or silent.** A figure is shown only with its method; a call without a defensible baseline
  contributes cost and no saving. (Constitution V *Token Discipline*; project memory: savings must be defensible.)
- **Cairn ships to everyone.** Nothing depends on a third-party local tool; the ledger uses only data
  Cairn itself produces and captures (Constitution III/IV).
- **Deterministic.** No model call is needed to record or report (Constitution II).
- **Shows value, never decoration.** Every pixel in the UI answers "what did Cairn save me, and how do I
  know" (project memory: UI must show value).

## Definitions (normative — the rest of the spec uses these exact terms)

- **Call**: one invocation of a Cairn capability by an agent or a person: an MCP tool, a CLI command, a
  session hook injection, or an API token request. UI page loads are *not* calls.
- **Sent tokens**: tokens of the exact text delivered to the caller for that call, measured once at the
  delivery boundary (after any budget trimming, including footers), using the **stated tokenizer**.
- **Stated tokenizer**: a named, versioned counting method recorded on every row. Default `bytes4-v1`
  (UTF-8 bytes ÷ 4, rounded up, minimum 1). Replaceable only by recording a new id; old rows keep theirs.
- **Replacement set** `R(call)`: the repository files, with line ranges where known, whose content the
  answer was derived from (the evidence the pack cites). Empty when the answer does not come from files
  (status, remember, recall of a stored memory, briefings).
- **Slice baseline** `B_low`: tokens of the cited line ranges plus 20 lines of context each side, merged
  per file — what an agent would read after grepping straight to the right place. Whole file where no
  range is known.
- **Read baseline** `B_high`: tokens of each file in `R`, each capped at the agent read limit (2,000
  lines or 40,000 tokens per file, whichever is lower; configurable) — what an agent would read opening
  the same files in full.
- **First-touch rule**: within one session, a file's baseline is credited at most once. The first Cairn
  call that covers a file carries its baseline; later calls over the same file carry zero for it (the
  agent would have read it once and kept it).
- **Observed re-read**: when Cairn's own session capture shows the agent read a file in `R` after the
  call, that file's baseline is removed from that call's saving — the agent paid for it anyway.
- **Saved range** for a call: `[max(0, B_low' − sent), max(0, B_high' − sent)]` where `B'` apply the
  first-touch rule and observed re-reads. `high ≥ low ≥ 0` always.
- **Overhead**: tokens Cairn itself costs the agent: session briefings, the MCP tool-schema block, and
  calls with an empty replacement set (their sent tokens). Model-call tokens from the `ledger` table are
  reported alongside, never subtracted silently (they are Cairn's, not the agent's context).
- **Net saved range**: summed saved range minus overhead, floored at zero. **Coverage**: share of calls
  that had a non-empty replacement set.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Every call is recorded (Priority: P1)

An agent calls Cairn eight different ways in one session — `cairn_context`, `cairn_impact`, `cairn_search`,
`cairn_recall`, `cairn_remember`, `cairn_status`, a generated graph tool and a CLI `cairn why` — and a
session hook injects a briefing. Priya wants every one of them accounted for.

**Why this priority**: Nothing else is trustworthy if calls go missing or are counted twice.

**Independent Test**: Enumerate every tool the MCP server can advertise in every mode, call each once
against a fixture repository, run each CLI command that returns context, and fire the session hook.
Exactly one ledger row exists per call, each with sent tokens, tokenizer id, surface, tool name, outcome
and timing; none has a second row.

**Acceptance Scenarios**:

1. **Given** a new MCP tool is added to any registry, **When** the test suite runs, **Then** a coverage
   test fails unless the tool writes exactly one row (no tool escapes by omission).
2. **Given** `cairn_context` or `ask`, **When** it is called once, **Then** one row exists, with a
   replacement set (not two rows, one without).
3. **Given** the store is busy or read-only, **When** a call is made, **Then** the call still succeeds and
   the missed write is counted in a `ledger_dropped` counter shown by `cairn doctor`.
4. **Given** a call that errors or finds nothing, **When** it returns, **Then** a row exists with outcome
   `error` or `empty`, sent tokens of what was returned, and no replacement set.

---

### User Story 2 — An honest estimate per call (Priority: P1)

For `cairn_impact src/pay.py`, Priya sees what it sent, which files it replaced and how big they are, and a
saved *range* with the words "estimated".

**Independent Test**: With a fixture repository of known file sizes, call `impact` and assert: slice and read
baselines equal hand-computed values; a repeat call over the same files in the same session credits zero
for them; a call with an empty replacement set records no saving; a pack larger than its baseline shows
saved `0`, not a negative.

**Acceptance Scenarios**:

1. **Given** a call citing a 5,000-line file, **When** the baseline is computed, **Then** `B_high` counts at
   most the read cap and `B_low` counts only the cited ranges plus context.
2. **Given** two calls in one session citing the same file, **When** both are recorded, **Then** the file's
   baseline is credited once, to the earlier call.
3. **Given** captured session events showing the agent opened a cited file after the call, **When** savings
   are reported, **Then** that file's baseline is removed from the call and the row says "re-read by agent".
4. **Given** no session capture is available, **When** reporting, **Then** re-read adjustment is marked
   "not applied" and the range is reported as wider, never narrower, than the adjusted one.
5. **Given** a call with no file baseline (status, remember, recall of a memory, briefing), **When**
   reported, **Then** it shows cost only and a dash for saved, never zero-saved-looking-good or a guess.

---

### User Story 3 — A command that answers "what did Cairn save me" (Priority: P1)

Priya runs `cairn savings` and gets total, per-session and per-call views in the terminal; `cairn status`
carries one line; `cairn recap` shows the same numbers.

**Independent Test**: Seed a store with known calls across two sessions; `cairn savings`, `cairn savings
--session last`, `cairn savings --calls`, and `--json` print values equal to hand-computed ones, and the
total equals the sum of the sessions equals the sum of the calls.

**Acceptance Scenarios**:

1. **Given** calls in two sessions, **When** `cairn savings` runs, **Then** it prints net saved range,
   gross range, overhead, call count, coverage, window, tokenizer, and a validation status line.
2. **Given** `--calls`, **When** run, **Then** each call prints time, tool, sent, replaced files, baseline
   range, saved range and flags (`first-touch`, `re-read`, `no-baseline`, `error`).
3. **Given** `cairn status` and `cairn recap`, **When** run, **Then** both quote the figure from the same
   function as `cairn savings` (one source of truth); a test fails if they can differ.
4. **Given** `--since 7d` or `--json`, **When** run, **Then** the window filters all totals and the JSON has
   the same fields the UI reads.

---

### User Story 4 — The Map UI shows value, with receipts (Priority: P2)

Priya opens the Savings page. The top says what Cairn saved her this week as a range with its coverage and
validation status. Below, sessions list; opening one shows each call and, per call, the replaced files with
their sizes beside what Cairn sent.

**Independent Test**: With a seeded store, the Savings page numbers equal `cairn savings --json`; opening a
call lists the same files and sizes as `--calls`; with an empty store the page explains how to get the first
number instead of showing zeros.

**Acceptance Scenarios**:

1. **Given** unvalidated estimates, **When** the page renders, **Then** the headline is the *low* bound with
   an "unvalidated" badge; the full range appears only after validation passes (US6).
2. **Given** any figure on the page, **When** the viewer asks "how is this computed", **Then** a method panel
   states the baselines, first-touch rule, tokenizer, what is excluded, and links the validation report.
3. **Given** calls without a baseline, **When** shown, **Then** they appear as cost, grouped, never inside the
   savings bars.
4. **Given** the Overview "Context served" card, **When** rendered, **Then** it shows the same net figure and
   links to the Savings page; the old gross-only bar is removed.
5. **Given** the page, **When** inspected, **Then** it contains no chart, animation or illustration that does
   not encode ledger data (rule from project memory).

---

### User Story 5 — Net of Cairn's own cost (Priority: P2)

Priya wants the number to include what Cairn costs her context: briefings, tool schemas, empty answers.

**Independent Test**: A session with one briefing, N turns and two empty answers reports overhead equal to
briefing tokens + schema tokens × turns + the empty answers' sent tokens, and net = gross − overhead, floored at 0.

**Acceptance Scenarios**:

1. **Given** captured turns for a session, **When** overhead is computed, **Then** schema tokens are charged
   per observed turn; without captured turns they are charged once and the figure is marked "lower bound of
   overhead" (so net is flagged "upper bound of net").
2. **Given** model calls in the existing `ledger`, **When** reported, **Then** they appear on a separate line
   ("Cairn's own model use"), never blended into agent-context numbers.

---

### User Story 6 — The estimate is validated against real agent usage (Priority: P2)

Before Cairn headlines a range, it has been compared with measured token use of real agent runs with and
without Cairn, and the result is shown next to the figure.

**Independent Test**: [validation-plan.md](validation-plan.md) runs end to end on the pilot suite and writes
a report; `cairn savings` reads the latest report and shows "validated: median error X% over N tasks" or
"not validated".

**Acceptance Scenarios**:

1. **Given** no validation report, **When** any surface shows savings, **Then** it shows "estimated, not yet
   validated" and uses the low bound as the headline.
2. **Given** a report whose median absolute error exceeds the gate (SC-004), **When** shown, **Then** the
   badge says so and the headline stays at the low bound.
3. **Given** a calibration factor derived from validation, **When** applied, **Then** it is recorded with the
   report id on every recomputed figure, and raw uncalibrated figures remain available.

---

### User Story 7 — Cost in money, only when the price is known (Priority: P3)

Priya wants dollars. Cairn converts tokens to money only with a price she configured or confirmed; otherwise
it shows tokens only.

**Independent Test**: With no price configured, no currency appears anywhere. With a price set, money equals
tokens × price, labelled with the price and date, input-side only.

**Acceptance Scenarios**:

1. **Given** `[savings] input_price_per_mtok` set, **When** reporting, **Then** money is shown beside tokens,
   labelled "at $X per million input tokens (configured)".
2. **Given** no price, **When** reporting, **Then** the UI offers where to set it and shows no number.

### Edge Cases

- Repo files change or vanish between call and report: sizes are recorded at call time with a content hash;
  reports use the recorded sizes.
- Very large replacement sets: store the top 50 files by size per call and a count of the rest.
- Concurrent agents on one store: rows are independent; session attribution never merges two sessions.
- Session unknown (a bare CLI call, an API token with no session id): grouped as "unattributed", still in totals,
  excluded from first-touch dedup across callers (each unattributed call is its own scope — conservative).
- Time skew and replays: idempotency key per call prevents duplicate rows on retried transport.
- Team server: rows carry the project and token name; one person's ledger is not visible to another without
  the same read role the Overview already requires.
- Ledger disabled (`[savings] enabled = false`): nothing is recorded, all surfaces say so; no partial data.
- Memory/recall answers drawn from stored memories: no file baseline, so cost only. We do not invent a baseline
  such as "what the user would have had to remember".

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record exactly one ledger row for every call (as defined), on every surface
  (MCP stdio, MCP over HTTP, CLI, session hook, API), including errors and empty results.
- **FR-002**: Each row MUST store: time, surface, tool/command name, session key (or unattributed),
  outcome, sent tokens, tokenizer id, duration, a truncated target (≤200 chars, as today), and the
  replacement set (path, file tokens, cited-range tokens, content hash, per file; top 50 + count).
- **FR-003**: Sent tokens MUST be measured on the exact text delivered, once, at the delivery boundary; no
  second render for counting.
- **FR-004**: A coverage test MUST fail when any registered MCP tool or context-returning CLI command can
  return without writing a row.
- **FR-005**: The system MUST compute `B_low`, `B_high`, first-touch credit and observed re-read as defined, and
  store the inputs so any figure can be recomputed under a different tokenizer or cap.
- **FR-006**: The system MUST NOT record a saving for a call whose replacement set is empty, and MUST NOT
  display a negative saving; saved is floored at zero per call.
- **FR-007**: Every displayed saving MUST be labelled *estimated*, show a range (or the low bound alone while
  unvalidated), show coverage, and name the tokenizer.
- **FR-008**: `cairn savings` MUST provide total, per-session and per-call views, `--since`, `--json`, with
  totals reconciling exactly (total = Σ sessions = Σ calls).
- **FR-009**: `cairn status`, `cairn recap`, the Overview card and the Savings page MUST read the figure from
  one function; a test MUST fail if their outputs can differ for the same store.
- **FR-010**: The Map UI MUST provide a Savings page with: net headline (range/low bound, coverage,
  validation badge), a session list, a per-call drill-down listing the replaced files with sizes beside the
  sent size, a cost-only group, a method panel, and an empty state that tells how to get a first number.
- **FR-011**: Overhead (briefings, MCP schema block, empty/error answers) MUST be subtracted to give a net
  figure; Cairn's own model-call tokens MUST be shown separately.
- **FR-012**: Session attribution MUST use, in order: an id the agent supplies (HTTP MCP session id, hook
  session id); otherwise the Cairn-captured session whose working directory and time window contain the call;
  otherwise "unattributed".
- **FR-013**: Observed re-read MUST use only Cairn's own session capture; when capture is absent the figure
  MUST be marked "re-read not applied".
- **FR-014**: Recording MUST never fail or slow a call: p95 added latency ≤ 5 ms, failures swallowed and
  counted; `cairn doctor` reports `ledger_dropped` and the ledger's size.
- **FR-015**: A validation harness MUST exist as specified in [validation-plan.md](validation-plan.md); its
  latest report MUST gate the wording and headline per US6.
- **FR-016**: Money MUST be shown only from a configured price, input-side only, labelled with price and date.
- **FR-017**: `cairn graph benchmark` MUST NOT feed the ledger and MUST stop inventing a corpus size: with no
  measured corpus it reports "unknown" rather than `nodes × 50`; its output MUST be labelled "whole-corpus
  upper bound, not a per-call saving".
- **FR-018**: The graph engine's JSONL query log MUST either write through the same recorder or be removed
  from the savings path so a call is never recorded in two systems.
- **FR-019**: Existing `queries` rows MUST be migrated: kept, marked `legacy` (no tokenizer id, uncapped
  baseline), excluded from net figures and shown only as a footnote, because they cannot be recomputed.
- **FR-020**: Nothing in this feature MAY depend on a third-party local tool; every input is Cairn's own
  store, capture or configuration.
- **FR-021**: The ledger MUST store sizes and paths only, never returned text or file contents.

### Key Entities

- **Call record**: one row per call (FR-002).
- **Replacement entry**: a file in a call's replacement set: path, file tokens, range tokens, hash.
- **Session**: a captured agent session, or the "unattributed" scope.
- **Validation report**: a dated artefact: suite id, runs, predicted vs measured, error statistics, calibration,
  gate result, tokenizer and cap used.
- **Savings summary**: the computed net range, gross range, overhead, coverage, window; the one object every
  surface reads.

## Success Criteria *(mandatory)*

- **SC-001**: Coverage — 100% of advertised MCP tools and context-returning CLI commands write exactly one
  row per call (enforced by test; adding a tool without a row fails CI).
- **SC-002**: Reconciliation — for any store, total = Σ sessions = Σ calls, and CLI, API and UI figures are
  identical (enforced by test).
- **SC-003**: Conservatism — for every call, `0 ≤ low ≤ high`; no call with an empty replacement set shows a
  saving; no file is credited twice in a session (property tests).
- **SC-004**: Accuracy gate — over a validation suite of ≥ 20 paired tasks, the median absolute error of the
  ledger's net saved estimate against measured token difference is ≤ 25%, and the measured value falls inside
  the reported range for ≥ 80% of tasks. Until met, the headline is the low bound and carries "unvalidated".
- **SC-005**: Reproducibility — recomputing a past figure from stored inputs with the same tokenizer and cap
  gives the same number to the token.
- **SC-006**: Cost of recording — p95 added latency ≤ 5 ms per call; zero call failures attributable to the ledger.
- **SC-007**: Comprehension — a reader shown the Savings page can say, unaided, what was saved, over what
  baseline, and what it does not include (checked with 3 reviewers against a written rubric).

## Assumptions

- The default tokenizer stays `bytes4-v1` until validation (US6) shows its error by file type; Cairn does not
  bundle a model tokenizer.
- "Agent reading the same files directly" is the counterfactual; web search, test runs and other tool use are out of scope.
- A single agent reads a file once per session and keeps it (hence first-touch). If validation shows frequent
  re-reads after context compaction, the rule is revisited, not silently loosened.
- The Claude Code read limit (2,000 lines) is the cap's default; other agents may differ and the cap is configurable.
- Savings in cached tokens are not distinguished from fresh tokens in this version; money (US7) is input-side
  at one configured price.

## Out of Scope

- Estimating output-token or latency savings; measuring answer quality.
- Counting what Cairn's cached or compacted context saves inside a vendor's prompt cache.
- Cross-repository or cross-machine aggregation beyond what the team server already serves per project.
- Any claim in marketing copy; the landing page may quote only a figure that carries a validation report id.

## Clarifications

Resolved from the code and project memory on 2026-10-16 (full list of what still needs the user:
[open-questions.md](open-questions.md)).

- Q: Reuse the `queries` table? → A: Extend the model (new fields and a replacement-set table) and migrate;
  legacy rows are kept but excluded from net figures (FR-019). Choice of table layout belongs to the plan.
- Q: Is the headline one number? → A: No. A range, with the low bound as the headline until validated (FR-007, US6).
- Q: Does the UI's own use count? → A: No, as today (nobody is "handed" the pack).
- Q: Where does "per session" come from without an id on stdio MCP? → A: FR-012 ladder; unattributed is a first-class bucket.
- Q: Model tokenizer? → A: Not bundled; calibrated offline in validation (Level 1) and reported as an error band.
- Q: Should briefings count as savings? → A: No, cost only, as today.
