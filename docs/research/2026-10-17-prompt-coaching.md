# Prompt coaching in Cairn: what is possible, from which open source, and how disruptive

Date: 2026-10-17. Status: research, no code changed. Labels: **READ** = read from a cited source this session; **INFERRED** = my reasoning, to verify before building.

Method: no third-party code was cloned, installed or run. Repositories were read through `gh api` (metadata, README, file lists) only, and their content was treated as untrusted data. The only local data touched was `spreix-notetaker/.cairn/sessions.db`, opened read-only; only counts and structure are reported, never prompt text.

## 1. Recommendation (short)

Build **Option A: a deterministic "prompt-quality" scorer over data Cairn already stores, surfaced as one line in the Sessions view and an opt-in weekly digest**. Effort M, no new dependency, no model tokens, no prompt text leaves the machine. Add **Option B (ambient nudge at prompt time)** only as an opt-in, and treat any model-written rewrite (**Option D**) as a later, opt-in, local-only experiment. Reasons: Cairn's constitution already says "Deterministic First, Models Second (NON-NEGOTIABLE)" (READ: Cairn brief, principle II), and the closest existing tools (below) are almost all model-based rewrite gates, which cost tokens and annoy users on every prompt.

## 2. What Cairn already has

| Fact | Source |
|---|---|
| Hooks capture every prompt (`UserPromptSubmit` → `session-init`), every tool event (`PostToolUse`), and the turn end (`Stop`); only stdlib, never fails the agent | READ: docs/agents.md "Session capture" |
| `user_prompts(content_session_id, prompt_number, prompt_text, created_at_epoch)`; `tool_uses(tool_name, tool_input, tool_response, prompt_number, content_session_id, observation_id)`; `observations` (type, files_read, files_modified, prompt_number); `session_summaries` (request, completed, next_steps); `sdk_sessions` (branch, cwd) | READ: src/cairn/engines/recall/schema.py:66-232 |
| Prompts are stored capped at 4000 chars; `<private>…</private>` is never stored; credential-looking values (`API_KEY=`, `--password`, `Bearer`) are masked before writing | READ: recall/tags.py:33; docs/agents.md "Privacy" |
| `tool_uses` already links each tool event to its prompt number, so "what happened after prompt N" is a join, not a parse | READ: schema.py `tool_uses.prompt_number` |
| An ambient `UserPromptSubmit` hook already exists: kill-switch `[context] ambient`, a code-relevance gate (`_plausibly_code`: min 20 chars, path/CamelCase/snake_case regex or action verbs), hard size cap, read-only, no model, any exception → None | READ: src/cairn/hooks.py:250-305 |
| Session start injects the brief (`Cairn.brief`, ~550 tokens, ordered lines filled to a budget) and a session-memory timeline | READ: core.py:676-720; docs/agents.md "Memory at session start" |
| Status line is one short line (layers, spec progress, drift, model) | READ: hooks.py:318-345 |
| Memories, specs, map symbols and git history are queryable without a model (`infer_targets`, `memory.recall`) | READ: hooks.py `ambient()`; docs/architecture.md |

Real-data shape (INFERRED as representative; one repo, one user): spreix-notetaker has 49 sessions, 262 prompts, 14,886 tool events, 256 summaries. Mean 5.3 prompts per session (max 147). Tool names are stored with mixed case (`Bash` 9,601 vs `bash` 2,523, `Read`/`read`, `Edit`/`edit`), so any detector must lowercase them. Every tool event has a `prompt_number`.

## 3. Open source and published work surveyed (12 candidates)

All metadata below is READ via `gh api repos/<owner>/<repo>` on 2026-10-17 unless noted.

| # | Project | License | Activity | What it really does | Fit for Cairn |
|---|---|---|---|---|---|
| 1 | [microsoft/SkillOpt](https://github.com/microsoft/SkillOpt) (seed) | MIT | 18.5k stars, pushed 2026-10-06 | Optimizes reusable *skills* (markdown) for frozen agents from trajectories, with validation-gated edits. v0.2.0 adds **SkillOpt-Sleep**: nightly harvest of Claude Code/Codex/Copilot/Cursor transcripts → mine recurring tasks → replay → gate → staged proposal you adopt. It improves the *agent's skill file*, not the user's prompt. README states a real backend sends truncated transcript excerpts to the provider and outbound text is "not guaranteed to be secret-free". | Idea source: harvest → mine → stage-for-review loop and the "inspect/redact then mark reviewed" workflow. Not a dependency (needs a model and replay budget). |
| 2 | [severity1/claude-code-prompt-improver](https://github.com/severity1/claude-code-prompt-improver) | MIT | 1.9k stars, pushed 2026-10-01 | A `UserPromptSubmit` hook that injects nudges into the model every prompt; for vague prompts it invokes a skill that researches and asks 1-6 clarifying questions. The judgment is the main model's, so it spends the user's tokens on every prompt (README says v0.4.0 cut tokens 31%). | Closest product. Shows demand, and shows the annoyance/cost risk of always-on model gating. |
| 3 | [nuipawarit/claude-coach](https://github.com/nuipawarit/claude-coach) | MIT | 0 stars, pushed 2026-08-17 | "prompting-coach": a ~1.2 KB rubric given to the running model; for severely under-specified prompts it opens a chooser with a rewritten prompt. Claims no second model call. | Pattern: fire only on gaps where guessing wrong wastes real work; one-keypress accept. |
| 4 | [ruijie-he/claude-coach](https://github.com/ruijie-he/claude-coach) | none stated | 0 stars, pushed 2026-04-16 | `/coach` command: a Python script reads `~/.claude/projects/**/*.jsonl`, the model compares the last 24h of prompts to a tips file, tags suggestions `[NEW]`/`[RECURRING since date]`, saves a report locally. | Pattern: recurring-vs-new tracking and "rewrite your actual prompts". No license, so ideas only. |
| 5 | [HenrikBrehm/prompt-refiner-skill](https://github.com/HenrikBrehm/prompt-refiner-skill) | MIT | 2 stars, pushed 2026-04-29 | A hybrid prompt *linter*: deterministic Node detector for regex-able rules, model for semantic ones; "lint, don't rewrite"; citeable rule catalog. | Best design match: deterministic-first, rule ids, lint not rewrite. Rule catalog is a reference for check ideas. |
| 6 | [05satyam/instruct_vault](https://github.com/05satyam/instruct_vault) | "other" per API | 5 stars, pushed 2026-08-08 | Git-native linting/testing/versioning for prompts *in application code* (YAML prompts, CI). Not for chat prompts. | Different problem. Skip. |
| 7 | [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | MIT | 26k stars, pushed today | Prompt/agent/RAG evaluation and red-teaming harness. | Useful only to *validate* a future model-based coach. Not for coaching users. |
| 8 | [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | MIT | 38.7k stars, pushed 2026-10-16 | Programming-not-prompting framework with prompt/weight optimizers for LLM pipelines. | Optimizes developer-authored pipelines, not human chat prompts. Skip. |
| 9 | [microsoft/PromptWizard](https://github.com/microsoft/PromptWizard) | MIT | 4k stars, last push 2025-10-13 (stale 12 months) | Task-aware prompt optimization with feedback-driven self-evolution (paper). Needs a labeled task. | Same class as DSPy. Skip. |
| 10 | [langfuse/langfuse](https://github.com/langfuse/langfuse) | "Other": MIT core, some directories under a separate license (LICENSE text read) | 36k stars, pushed 2026-10-16 | LLM observability, tracing, evals, prompt management for apps. | Overkill server stack; license is mixed. Reference for session/trace views only. |
| 11 | [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) | **Elastic License 2.0** (LICENSE read) | 11.9k stars, pushed today | AI observability and evaluation. | ELv2 is not OSI open source; do not embed. |
| 12 | [ccusage/ccusage](https://github.com/ccusage/ccusage) | MIT (LICENSE read) | 19k stars, pushed today | Reads Claude Code JSONL to report token/cost usage. | Shows users value local-transcript analytics; it does not judge prompt quality. |
| 13 | [disler/claude-code-hooks-multi-agent-observability](https://github.com/disler/claude-code-hooks-multi-agent-observability) | none stated | 1.5k stars, pushed 2026-02-08 | Hook-event monitoring dashboard. | Same hooks as Cairn; no quality scoring. Skip. |
| 14 | [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) | Apache-2.0 | 1.7k stars, pushed 2026-02-12 | Publishes transcripts as HTML. | Not coaching. Skip. |

Also checked and judged minor: `ndpvt-web/prompt-improver` (MIT, 87 stars, vague→specific skill), `hancengiz/claude-code-prompt-coach-skill` (no license, 148 stars), `msathiyakeerthi/claude-code-prompt-coach` (MIT, 2 stars), `victor-aguilars/promptocop` (MIT, 5 stars, prompt linter). I did not read their code; summaries come from repository descriptions only.

Published work (READ via web search snippets only, not full text; verify before citing):
- [How Coding Agents Fail Their Users (arXiv 2605.29442)](https://arxiv.org/pdf/2605.29442): analysis of 20,574 real sessions; the snippet shows an annotation scheme of intent, agent divergence, developer pushback and rework cost. Findings not confirmed by me.
- [Latitude, "how to detect user frustration in your llm agent"](https://latitude.so/blog/how-to-detect-user-frustration-in-your-llm-agent): a blog (secondary) claims keyword rules catch almost none of frustration and that it lives in the conversation's shape. Treat the number as unverified.
- [Tessl friction-review skill](https://tessl.io/registry/tessl-labs/audit-logs/files/skills/friction-review/SKILL.md): model judges review transcripts and aggregate friction.

Takeaway (INFERRED): everything in the Claude Code ecosystem that coaches prompts is either (a) a per-prompt model gate, or (b) an after-the-fact model review of raw JSONL. Nobody joins prompts to *what the agent then did* (retries, reverts, re-edits), which Cairn already stores. That join is the differentiated, deterministic signal. The keyword-recall warning also means correction wording alone is a weak detector; behavior after the prompt is stronger.

## 4. What Cairn can detect deterministically vs what needs a model

Real-data sanity check (counts only, crude regexes, one repo; INFERRED as indicative): of 262 prompts, 145 were at least 20 characters with no path/identifier-like token; 63 matched simple correction words ("no", "again", "revert", "instead", …); 36 were under 20 characters (chat); 3 hit the 4000-character storage cap; 50 (turn, file) pairs had a file edited 3 or more times in one prompt turn; 92 prompt turns had a Bash error marker; 3 sessions had 3 or more correction-marker prompts. These show the signals have usable base rates, not that they are accurate.

| Signal | Data used | Deterministic? | Notes / false-positive risk |
|---|---|---|---|
| First prompt of a session names no file, symbol, spec or ticket | `user_prompts` #1 vs map symbols (`infer_targets`) and spec ids | Yes | Many good prompts are conversational ("fix the failing test"). Count only prompts that start work (reuse `_plausibly_code` verbs). |
| Repeated corrective prompts in a session | `user_prompts` sequence; correction lexicon plus high token overlap with the previous prompt | Mostly. The lexicon is weak (see Latitude caveat), so use overlap and behavior as the main signal. | Report as a count per session, not a verdict. |
| High rework after a prompt | `tool_uses` per `prompt_number`: same file edited ≥3 times, Edit followed by revert/`git checkout`, failed Bash then retry | Yes | Normal TDD loops look like rework; require edits plus a following corrective prompt. |
| Large blast radius per prompt | files_modified count per prompt from `observations` | Yes | Suggest splitting; benign for refactors. |
| Prompt re-explains something already in memory | `memory.recall(prompt)` similarity above a threshold | Yes (local embeddings, no model call; Cairn's vectors are local per docs/architecture.md) | Gives the user a pointer: "memory:abc already says this, it is injected at session start". Needs a threshold calibration. |
| Prompt far longer than the evidence it cites | `len(prompt)` vs count of resolved paths/symbols | Yes | Heuristic. Cap-hit at 4000 chars is a certain flag. |
| Same question asked again across sessions | `user_prompts_fts` / vectors | Yes | Points to a missing memory (cairn_remember). |
| Missing acceptance criterion / ambiguity / "is this the right design question" | prompt semantics | **Needs a model** | Defer. |
| Suggested rewrite of the prompt | prompt text | **Needs a model** | Only local-first or on-demand, see privacy. |
| Why a correction happened (bad prompt vs bad agent) | transcript semantics | **Needs a model** | Cannot be told from counts. Say "rework" not "bad prompt". |

## 5. Three coaching surfaces (example text)

All text is INFERRED design; wording is a draft.

1. **Session-start brief line** (adds one line to `Cairn.brief`; the agent sees it, and the user sees it if the agent echoes it). Example:
   `Prompting, last 7 days: 6 of 9 sessions started without a file or symbol; those needed 2.3x more correction prompts. Naming the file (e.g. src/pay.py) up front is the cheapest fix.`
   Risk: this text goes to the *agent*, not the user, so it can waste tokens and confuse the agent; keep it out of the default brief. Prefer surfaces 2 and 3.
2. **Status line** (user sees it; one short segment, hidden when nothing to say):
   `▲ cairn · 1,204 files · 31 memories · prompt: 3 corrections this session`
   Cheap and visible, but easy to find nagging. Show only past a threshold, only in the user's own status line.
3. **UI Sessions view + weekly digest** (user-pulled, so no interruption). Example digest:
   ```
   Prompt review, 10-10 to 10-17 (computed on this machine, nothing sent)
   - 4 sessions began with no file named. They took 11 prompts on average vs 4.
   - 2 sessions: the same file was edited 5+ times after you restated the goal. Try: paste the failing test name.
   - You re-explained "the smoke test needs a real HOME" in 3 sessions. That is already memory:3492e3 — it is injected at start; no need to repeat.
   ```
   The "already in memory" line is value-adding and explainable, and matches the "UI must show value" feedback already recorded for this project (READ: project memory index).

A fourth, optional surface: a pre-submit nudge via the existing ambient hook (Option B). It is the most disruptive and should be off by default.

## 6. Options: effort, disruption, recommendation

| Option | What | Effort | Privacy | New deps | Model tokens | Noise / annoyance | Default |
|---|---|---|---|---|---|---|---|
| **A (recommended)** | Deterministic scorer over `user_prompts` + `tool_uses` + `observations`; shown in the Sessions view and an opt-in weekly digest | **M** (new module, a table or kv for scores, a UI card, tests, threshold tuning) | None leaves the machine; reads data already in `.cairn/sessions.db` | None | 0 | Low: user-pulled | On for the view, digest opt-in |
| B | Ambient nudge in the existing `UserPromptSubmit` hook ("name the file", "memory:x already covers this") | **S-M** (extend `hooks.ambient`, which already exists) | Same hook runs on the local prompt; output is injected into the agent's context | None | 0 for the check; the nudge text costs a few agent tokens per fire | **High risk**: fires at the moment the user is typing, can mislead the agent | Off; opt-in `[coach] ambient` |
| C | Status line segment and brief line (surface 1/2) | **S** | None | None | Brief line adds agent tokens every session | Medium; the status line is always visible | Off |
| D | Model-written rewrites or weekly "coach" report (pattern of claude-coach, prompt-improver, SkillOpt-Sleep) | **L** | **Prompt text goes to a model provider.** Violates "nothing leaves the machine" unless the model is local | Possibly a local model | Real spend (this project's weekly limit was burned before; see the token-budget memory) | Medium-high | Off, explicit per-run consent |
| E | Adopt SkillOpt-Sleep or a similar skill optimizer to improve the *agent's* skills from transcripts | **L** | Sends transcript excerpts to a provider; README admits no secret-free guarantee | Large Python dependency | High (replay budget) | Low for the user, but off-topic for "coach the user" | Off. Research only |

Why A over the others (INFERRED): it is the only option that is fully deterministic, adds zero tokens, adds no dependency, and gives the user a reason to open the UI. B reuses the existing hook but interrupts typing and adds agent tokens. D and E contradict principles II and V (deterministic first, token discipline) unless made explicit opt-in.

Suggested build order: (1) a pure function `score_session(db, session_id) -> list[Finding]` returning counts and rule ids only, never prompt text; (2) fixture tests on synthetic sessions (including lower-cased tool names and the 4000-char cap); (3) the Sessions view card; (4) the weekly digest, opt-in; (5) later, B behind a flag. Calibrate thresholds on two repos before shipping any wording that says "bad prompt". Frame findings as rework signals, not judgments.

## 7. Privacy: what must never leave the machine

Prompts can contain secrets, customer data, internal URLs, unreleased ideas. Cairn masks known credential shapes and honors `<private>`, but that masking is pattern-based and cannot be complete (the SkillOpt README makes the same admission about its own redaction; READ).

Rules for any coaching feature:
- **Never leave the machine**: prompt text, tool inputs/outputs, file paths with client names, memory text, and any score tied to a named person. Do not log them to telemetry, issue reports or a shared team server by default.
- **Default features (Option A) send nothing**; digests are generated and stored locally under `.cairn/`.
- **Team mode** (INFERRED, per docs/teams.md existence): aggregate per-person coaching scores must not be visible to other members or admins. Prompting stats are personal; ship them per user, never in team dashboards or the audit log.
- **Model-based options** (D, E) require: explicit per-run consent, a local model or the user's own CLI session, passing only the redacted excerpt that the user has reviewed (the SkillOpt "harvest to a file, inspect/redact, mark reviewed, then replay" flow is a good template), and an on-screen note of what will be sent. Store consent state; show a dry-run of the payload size.
- **Stored findings** hold rule ids, counts and `prompt_number`, never prompt text, so deleting prompts also deletes the coaching trail.
- **Do not shame**: no leaderboards, no cross-user comparisons.

## 8. Open questions for the owner

1. Is coaching strictly per-user (recommended), or should a team lead see aggregates? (INFERRED: aggregates carry the highest privacy and morale risk.)
2. Is a weak rule acceptable if clearly labeled "rework signal"? The keyword approach is known to be weak; behavior-based signals need calibration on more repos than the one sampled here.
3. Do you want the agent to see coaching text at all (brief line), or only the human?
4. Is any model use acceptable for the weekly digest, if it runs through the user's own signed-in CLI and only on a reviewed redacted excerpt?

## 9. Limits of this research

- One local database (one user, one repo) sampled by crude regex counts; base rates are indicative only.
- Third-party projects were read through READMEs, file lists and API metadata, not code audits. "What it really does" comes from their own documentation; claims such as token reductions are the authors' own.
- The arXiv paper and blogs were seen only as search snippets.
- Licenses: for langfuse, phoenix and ccusage the API returns "other", so I read the LICENSE text start; verify full terms before any reuse. Projects with no license (claude-coach, prompt-coach-skill, the observability dashboard) must not be copied.
