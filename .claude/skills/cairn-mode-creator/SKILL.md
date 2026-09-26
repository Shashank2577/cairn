---
name: cairn-mode-creator
description: Interactively create, install, activate, and verify custom Cairn session-memory modes, including domain-specific observation types, concept tags, and startup-context verification. Use this whenever someone asks to customize what Cairn's session memory remembers, create or change a mode, track domain-specific notes, or add observation types or tags—even if they do not use the word "mode."
compatibility: Requires a local Cairn installation (Python), an interactive question tool, and filesystem access.
---

# Mode Creator

Create a useful note-taking system, not merely a valid JSON file. Interview the user, propose a small taxonomy, obtain approval, install it durably, activate it, and prove the active mode appears in startup context.

## Ground rules

- Use the available interactive question tool (`AskUserQuestion`, `request_user_input`, or equivalent) for the interview. Ask in small batches and wait for each response.
- Explain observation types as mutually exclusive kinds of notes and concepts as reusable tags. Avoid jargon unless the user uses it first.
- Inspect existing bundled and user modes before inventing a new one. Reuse or remix a close match when that serves the user better.
- Do not edit a bundled mode (the ones shipped inside the Cairn package). Install custom files under the user modes directory: `$CAIRN_HOME/modes` (default `~/.cairn/modes`), or `$CAIRN_RECALL_MODES_DIR` when that is set.
- Preserve unrelated settings. The installer makes timestamped backups before it changes anything.
- Custom modes are supported by the local runtime. If `runtime` in the `[recall]` section of `.cairn/config.toml` is `server`, explain that this workflow cannot safely install a per-user mode into the shared server and stop before mutation.
- Existing observations keep their original types. The new mode applies to future observation generation.

## 1. Open with the purpose

Begin with this message inside the first interactive question:

> Custom modes let you take notes for whatever you're working on. If you're a law student, you may want to write down every time a case establishes a rule, a professor flags an exam trap, or doctrines conflict. If you're an architect, you may want to capture every design decision, code constraint, client preference, or site discovery. What are you working on?

Do not start by asking for a mode name or JSON fields. Learn the work first.

If the answer is code-related, say:

> Code mode already works well for software work. A custom variant may work better if it also tracks [2–4 specific kinds of notes inferred from their work] and tags [2–4 useful cross-cutting themes]. Would you like to keep standard code mode or customize it?

Use concrete suggestions. For an ML platform engineer, for example, suggest experiment outcomes, data-contract changes, production incidents, model decisions, cost findings, and reproducibility risks—not generic “custom notes.” If the user chooses standard code mode, do not create a redundant file; continue to the verification step.

## 2. Discover what is worth remembering

Use follow-up questions to obtain:

1. Three examples of moments or findings they would want available next week.
2. Routine activity that should be skipped.
3. The nouns and decisions they search for later: people, cases, materials, clients, constraints, experiments, incidents, and so on.
4. Anything sensitive that should never be recorded.
5. Whether notes should be selective or detailed.

Infer answers already present in the conversation instead of asking twice. When the user gives a broad answer, propose examples and let them select or edit them.

## 3. Propose the mode

Read [references/mode-authoring.md](references/mode-authoring.md) before drafting.

Propose:

- A clear mode name and lowercase ID.
- Usually 4–8 observation types. Each observed item gets exactly one type.
- Usually 4–8 concept tags. An item may get several concepts.
- One-sentence recording and skipping policies.
- Two realistic notes the mode would record and two it would skip.

Present the proposal in plain language and use the interactive question tool for approval. Let the user rename, add, remove, or reword categories. Do not write or install until they approve the taxonomy and privacy boundary.

Prefer an inherited ID such as `code--architecture-practice` so the mode reuses Cairn's stable output protocol while replacing the domain taxonomy and behavioral prompts. The `code` parent is an implementation base; the override must remove code-specific semantics from the prompts. Use a standalone mode only when inheritance is genuinely unsuitable.

## 4. Draft, validate, and install

Resolve the absolute directory containing this `SKILL.md`; all helper paths are relative to that directory. Run the helper with the Python environment Cairn is installed in (the interpreter named in the Cairn hook commands, e.g. in `.claude/settings.json`).

Write the approved mode to a temporary JSON file. Use the exact inherited override shape in the authoring reference. Then validate without mutating anything:

```bash
python3 <skill-directory>/scripts/install_mode.py \
  --mode <temporary-mode.json> \
  --mode-id <parent--custom-id> \
  --dry-run
```

Fix every validation error before installation. Then install and activate it for the repository:

```bash
python3 <skill-directory>/scripts/install_mode.py \
  --mode <temporary-mode.json> \
  --mode-id <parent--custom-id> \
  --root <repository-root>
```

The installer:

- Merges the override with its parent and validates the complete mode.
- Installs the source override under the user modes directory (`$CAIRN_HOME/modes/`).
- Sets `mode` in the `[recall]` section of `<repository-root>/.cairn/config.toml` (omit activation with `--no-activate`).
- Writes atomically and reports any backup paths (under `$CAIRN_HOME/backups/`).

Review its JSON result. Do not claim success if `ok` is not `true`.

## 5. Restart and prove the result

Read the configured runtime before verifying. The mode is loaded when observations are generated; if a long-running Cairn server or recall worker is running for this repository, restart it so it re-reads `[recall] mode` (stop and start `cairn ui`, or whatever process hosts the server).

Verify all of the following:

1. The installed file exists under the user modes directory.
2. `.cairn/config.toml` names the intended `mode` in `[recall]`.
3. Request full startup context with the `session_start_context` MCP tool (full context) when available. Otherwise call the Cairn server route `/api/recall/context/inject?project=mode-creator-verification&full=true` on the configured local server.
4. Startup context contains `Mode: <mode name> (<mode id>)`.

If the context falls back to `code`, inspect the recall worker log (`.cairn/recall/worker.log`) for a mode validation or lookup error, repair the mode, and repeat. Do not describe a fallback as successful activation.

## 6. Hand off clearly

Conclude with:

- Active mode name and ID.
- Installed path.
- Observation types and concepts.
- Restart and startup-context verification result.
- Backup paths for rollback.
- One short example of what the new mode will now remember.
