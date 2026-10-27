# What Cairn can take from three repositories, and what else could make it a complete cycle

Date: 2026-10-17. Revised after the owner clarified the question: not "can Cairn depend on these", but "what can Cairn adopt from them so it covers the whole development cycle". The first version of this report judged compatibility; this one judges value.

Method: read-only, through `gh api`. Nothing was cloned, installed or run. Repository content was treated as untrusted data. Labels: **READ** = read from the cited source on 2026-10-17. **INFERRED** = my reasoning, to verify before building.

Related reports in this folder: [missing-oss](2026-10-17-missing-oss.md) (secret-masking gaps, linters, benchmarks) and [prompt-coaching](2026-10-17-prompt-coaching.md). This report does not repeat them.

## 1. The idea, restated

1. **Complete the cycle.** Cairn already helps with specify, plan, tasks and implement. Add **test** and **review**, and let review results flow into memory so the next session starts smarter.
2. **Personal friction memory.** Learn which problems a person keeps hitting in a given repository (example: Lightsail SSH timing out from a new network because the firewall only allows known IPs) and surface the fix at the right moment.
3. **A "wow" layer** built from what only Cairn has: the memory, the spec-to-code links, the decision records and the history.

## 2. Cairn today (the base everything attaches to)

- READ (docs/architecture.md): five engines feed one read model, `.cairn/brain.db`. Model calls go through `router.py` and are recorded in a ledger.
- READ (docs/agents.md): hooks capture prompts, tool events and turn ends into `.cairn/sessions.db`. A `cairn-reviewer` sub-agent exists, "reviews a diff against specs, history and conventions". `cairn drift` checks code against specs.
- READ (constitution v1.1.0): deterministic first, zero friction, token discipline, local-first, agent-neutral, and engines stay invisible (attribution in `NOTICE` and `licenses/` only).
- READ (src/cairn/engines/recall/fallback.py, listing of function names): the deterministic fallback builds records from tool events. I saw no function that pairs a failure with the fix that followed. INFERRED: that pairing does not exist yet. I did not read the file body, so check before building.
- READ (docs/research/2026-10-17-prompt-coaching.md §2): `tool_uses` links every tool event to its prompt number and stores its response, so "what failed, and what happened next" is a query over data Cairn already keeps.
- Gap: nothing in Cairn runs tests as a stage, and nothing reviews code for defects line by line.

## 3. open-code-review (OCR): adopt it as the review stage

**What it is.** READ ([README](https://github.com/alibaba/open-code-review)): a Go CLI, `ocr`, that reviews a git diff. Deterministic code picks and bundles files and matches rules to them; an agent reviews each bundle; separate modules fix comment positions and reflect on content. It outputs JSON, supports full-file `ocr scan`, and has CI actions for GitHub, GitLab and Gerrit.

**License and health.** READ: Apache-2.0, the same as Cairn (pyproject `license = "Apache-2.0"`). 47.5k stars, 100+ contributors, pushed today, releases v1.12.13 to v1.13.1 in nine days, 316 open issues. Very active; high churn.

**Why it fits better than I first said.** Cairn already vendors engines under `licenses/` and keeps them invisible. OCR can be the review engine behind a `cairn review` command in the same way.

**The two pieces that make the join work (both READ):**

1. *Delegation mode* ([skills/open-code-review-delegate/SKILL.md](https://github.com/alibaba/open-code-review/blob/main/skills/open-code-review-delegate/SKILL.md)). `ocr delegate preview --format json` lists the reviewable files and the files excluded, with reasons. `ocr delegate rule --format json <paths>` returns the matching rules grouped by rule text. The host agent then does the reviewing with its own model. The skill states OCR needs no LLM endpoint in this mode. So: no second API key, no review spend outside the user's normal agent usage.
2. *Rules are plain data.* `.opencodereview/rule.json` is a list of `{path, rule, merge_system_rule}` entries, where `rule` is free text ([example in the OCR repo itself](https://github.com/alibaba/open-code-review/blob/main/.opencodereview/rule.json): style, doc-sync and test-coverage requirements per file path). INFERRED: Cairn can generate this file from its own memory. Conventions, gotchas and past incident fixes already have file links in the read model, so each can become a path-scoped rule. A convention such as "`/graph/views` must list engine views only when graph.json exists" would be attached to the files it concerns.

**What Cairn adds to OCR (INFERRED, from READ facts on both sides).** OCR's README describes diff, file contents and codebase search only; it has no notion of specs, decisions or incident history. Cairn can supply: the spec task that owns the change, files that usually change together and are missing from the diff, earlier fixes and reverts in the same code, and open drift. A finding then reads "this repeats the regression fixed in commit X" instead of a generic warning.

**What review adds to Cairn (INFERRED).** Each finding the user accepts or dismisses is a labelled example. Accepted ones that recur become `gotcha` or `convention` memories (existing kinds); dismissed ones become suppressions. Both feed the next brief. This is the loop the owner described.

**Options.**

| Option | Effort | Notes |
|---|---|---|
| A. Delegation-mode review: `cairn review` runs `ocr delegate`, injects a Cairn context pack, the agent reviews, findings are stored | M | Detect `ocr` on PATH; offer to install it; no key, no new model path. Recommended first. |
| B. Generate `.opencodereview/rule.json` from Cairn memory | S to M | Cheap and valuable even if the user runs `ocr review` themselves. |
| C. OCR-managed mode (OCR calls its own LLM) | M | Works, but its tokens bypass Cairn's ledger and router. Allow only if the call can be routed through the ledger. |
| D. Port OCR's ideas into Python (bundling, rule matching, reflection pass) | L | Avoids the Go binary. Only worth it if packaging the binary proves painful. |
| E. Vendor and ship the binary inside the wheel | L | Per-platform builds. Revisit only if install friction hurts adoption. |

**Disruption (INFERRED unless marked).**
- A and B: no change for existing users unless they run the new command. No new Python dependency. One optional external binary (READ: needs Git 2.41 or newer, and Node or a release binary to install). Findings need storage: either new `events` rows of an existing kind or one small table; decide in the spec. No hook changes. Model tokens: those of the user's agent doing the review, which the user already pays.
- C: a second set of provider keys and a second session store at `~/.open-code-review` (READ: issue 1761 asks to change that path). Costs would not appear in `cairn models --ledger` unless integrated.
- Privacy: in delegation mode code goes only to the agent the user already uses. In OCR-managed mode it goes to whichever provider they configure there.

**Risks.** High release churn (pin a tested version range). READ: 316 open issues, including Windows console and orphaned-process bugs (1759, 1760). Findings quality is the maintainers' own benchmark claim (READ in README, not verified). Scope: Cairn would own a review product surface.

**Recommendation: adopt, via A then B.** Confidence: medium-high. Check these before the spec: how `ocr delegate` behaves in a repo with no rules, and what its JSON looks like (I read the skill description, not the code or sample output).

**Testing, the missing stage.** OCR does not cover it. Smallest honest step: record test results Cairn already sees in `PostToolUse` events (command contains `pytest`, `npm test` and so on; exit status and failing test names) as `test_run` facts, and let `cairn impact` list the tests to re-run (it already names tests, per docs/agents.md). Active tools such as `mutmut` (BSD-3, last push 2026-10-15) or `pytest-testmon` (MIT, last push 2025-12-01, older) could be offered as detected options later. READ: metadata only; I did not review their code.

## 4. SkillOpt and BuildPartner: the friction memory

**What each contributes (READ).**
- *SkillOpt-Sleep* ([docs/sleep/README.md](https://github.com/microsoft/SkillOpt/blob/main/docs/sleep/README.md)): harvest past sessions, mine recurring tasks, replay them, and consolidate changes behind a held-out validation gate, staged for the user to approve. MIT, preview status.
- *BuildPartner `improve-system`* ([SKILL.md](https://github.com/austinmarchese/buildpartner-plugin/blob/main/plugin/skills/improve-system/SKILL.md)): a skill that reads local session history and setup files, finds recurring patterns, and recommends fixes, with a rule not to flag a pattern from a single instance. It also nudges on a schedule (Monday and Friday). No license file; its server and coaching content are not in the repo. Idea source only.

**What Cairn can build on its own data (INFERRED).**

1. **Struggle detection, no model.** Over `tool_uses` and `user_prompts`, flag a struggle when the same failure signature recurs: an error line normalised (numbers, hosts and paths removed), or the same failing command, or an ssh/curl/docker/deploy command that fails several times in one session or across sessions. The Lightsail case would show up as repeated `ssh ... timed out` or `Connection refused` across sessions.
2. **Resolution pairing.** The fix is the first successful run of the same command class, plus the commands and edits between the failure and the success. Capture those as the proposed "what worked".
3. **Store as a memory** of kind `gotcha`, scoped to the repo, with citations to the sessions (`EXTRACTED`) and the pairing (`INFERRED`). Show it for the user to confirm before it is stored. This is SkillOpt's "review then adopt" idea and BuildPartner's "need a recurrence before calling it a pattern" rule, without their model cost.
4. **Surface it at two moments.** At session start (the brief already holds gotchas) and, more usefully, when a new failure matches a known signature: the existing ambient `UserPromptSubmit` hook (READ: prompt-coaching report §2, src/cairn/hooks.py:250-305) could add one line, "Last time this was the Lightsail firewall; add your current IP." A second hook is not needed, though a `PostToolUse` failure match would be the more precise trigger and needs a small, always-fast check. Decide in the spec.
5. **Environment memory.** For the "new environment" case, store the fix as a short procedure and the thing that changed (IP, region, key). Procedures such as "how to add my IP to the Lightsail firewall" are exactly what a person forgets.

**What not to take.** SkillOpt's replay-and-gate loop needs model spend and a scored task set; its own open issues say the nightly token cap is defined but never enforced (READ: microsoft/SkillOpt issues 313 and 317), which clashes with your recorded weekly-limit concern. Defer the gate. BuildPartner's global `CLAUDE.md` edits, usage upload, auto-allow permission hook and silent self-update (READ: its issue 2 and hooks) must not be copied.

**Options.**

| Option | Effort | Notes |
|---|---|---|
| A. Detect recurring failures, report only (CLI and Sessions view) | S | Read-only over existing tables. Proves the signal is real before anything is stored. Recommended first. |
| B. Resolution pairing and "confirm to save" gotcha memories | M | Uses existing memory kinds and supersession. |
| C. Match new failures against known gotchas and nudge in the moment | M | Adds latency-sensitive work on the hook path; keep it a local lookup. |
| D. Model-written summaries of why the fix worked | S to M | Optional enrichment on the fast tier, budgeted, as with other session records. |
| E. SkillOpt-style gate | L | Defer. |

**Disruption.** A is read-only. B writes memories the user confirms (no schema change if it reuses `memories`). C touches the hook path and must obey the existing "never fail the agent" and size-cap rules (READ: hooks.py gating). Privacy: error lines can contain hostnames and IPs; mask them with the improved patterns from the missing-oss report before storing, and keep everything in `.cairn/`.

**Recommendation: build A, then B.** Confidence: medium. The signal-to-noise ratio of failure signatures is the main unknown; A answers it on spreix-notetaker's real data without storing anything.

## 5. The "wow" layer: ideas only Cairn can do

Each uses data Cairn already has. None is built; all are INFERRED. Ordered by value for effort.

| # | Idea | Built from | Effort | Model needed |
|---|---|---|---|---|
| 1 | **"You have been here before"**: before an edit, warn if a similar change was reverted, fixed or flagged in review | `cairn_impact` history, risk tags, review findings | S | No |
| 2 | **Review that knows your decisions** (section 3) | memory, specs, rules from conventions | M | The agent's own |
| 3 | **Friction memory** (section 4) | sessions.db | S to M | No |
| 4 | **Spec-aware test advice**: the requirements a change touches and the tests that cover them; flag requirements with no test | specs, map, `tests_for` | M | No |
| 5 | **Architecture guard from the map**: a rule file ("service A must not import B") checked on sync, with the existing drift report. `import-linter` (BSD-2, pushed 2026-10-13) does this for Python and shows the pattern | map imports | M | No |
| 6 | **Pre-merge brief**: one page per branch combining risk, drift, review findings, tests run and the decisions it touches | everything above | M | Optional narration |
| 7 | **Onboarding pack**: new teammate or new machine gets the decisions, gotchas and top friction fixes for a repo | memory, friction memory | S | No |
| 8 | **Cross-repo lessons**: a fix learned in one repo is offered in another that has the same stack signature | multi-repo hub | L | No |

**Pull-request bots as another input.** READ: `The-PR-Agent/pr-agent` (MIT, 13.4k stars, Python, pushed 2026-10-16) is an alternative review engine on the same slot as OCR. I did not read it. If OCR's packaging proves hard, it is the second candidate to evaluate, since it is Python.

## 6. Ranked plan

| Rank | Item | Source | Effort | Disruption | Why first |
|---|---|---|---|---|---|
| 1 | Friction report (read-only) | SkillOpt, BuildPartner ideas | S | None | Proves the signal on real data; no storage, no tokens |
| 2 | Rules generated from memory (`rule.json`) | OCR | S to M | Very low | Works even if the user runs `ocr` themselves; shows the memory-to-review link |
| 3 | `cairn review` via OCR delegation mode, findings into memory | OCR | M | Low | Completes the cycle; no new keys |
| 4 | Test-run facts from tool events + requirement-to-test coverage | Cairn's own data | M | Low | Fills the testing gap |
| 5 | Resolution pairing and gotcha confirmation | SkillOpt idea | M | Low | Turns reports into memory |
| 6 | "Been here before" warning and pre-merge brief | Cairn's own data | S to M | Low | The visible wow |
| 7 | Architecture guard | import-linter idea | M | Low | Nice-to-have |
| 8 | Replay gate, cross-repo lessons | SkillOpt, hub | L | Medium | Defer |

## 7. Smallest first step

**Build the friction report (item 1).** One read-only command over `sessions.db`:

1. Select tool events with a failing response (a non-zero exit code or an error marker in `tool_response`); lowercase tool names first, because Cairn stores them with mixed case (READ: prompt-coaching report §2).
2. Normalise the error text and group identical signatures across sessions.
3. Print the top groups with counts, the sessions that back each (as `session:<id>` citations), and the first later success of the same command class as the candidate fix. Tag the pairing `INFERRED`.

Run it on spreix-notetaker first, since the Lightsail and transcription problems are known to be in its history. If it finds them, go on to item 2. Per the constitution a spec comes first, and `cairn_context` should be run on `src/cairn/engines/recall/` before any edit.

## 8. Open questions

- Which review stage do you want to own: an OCR-backed `cairn review` (recommended), or Cairn's own `cairn-reviewer` improved with OCR's ideas?
- May Cairn offer to install `ocr` (needs Node or a release binary, and Git 2.41 or newer), or only detect it? Principle III says detect, never demand.
- Should friction fixes be stored only after the user confirms (recommended), or automatically for obvious cases?

## 9. Limits of this research

I did not read OCR's JSON output format or its Go code, SkillOpt's `mine.py`, or Cairn's `fallback.py` body. I did not run anything. Maturity claims come from repository metadata and READMEs. Benchmarks claimed by OCR and SkillOpt are the authors' own and unverified. Tool names such as `mutmut`, `pytest-testmon`, `import-linter` and `pr-agent` are from metadata only.
