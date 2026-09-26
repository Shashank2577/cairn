# Bug Triage Workflow Extension

A three-step bug triage workflow for Cairn: assess, fix, and validate. Each bug lives in its own directory under `.cairn/workflow/bugs/<slug>/`, with one Markdown report per stage.

## Overview

This extension delivers an opinionated, repeatable bug workflow that any AI coding agent can drive:

1. **Assess** — read a bug report (pasted text or a URL), judge whether it is a real bug, locate suspected code paths, and propose a remediation.
2. **Fix** — apply the proposed remediation and record exactly what changed.
3. **Test** — re-run the reproduction and any added tests, then record the verification result.

The three stages communicate through three Markdown files in a single per-bug directory:

```
.cairn/workflow/bugs/<slug>/
├── assessment.md   # written by cairn.bug.assess
├── fix.md          # written by cairn.bug.fix
└── test.md         # written by cairn.bug.test
```

## Commands

| Command | Description | Output |
|---------|-------------|--------|
| `cairn.bug.assess` | Triages a bug report (pasted text or URL) against the codebase. | `.cairn/workflow/bugs/<slug>/assessment.md` |
| `cairn.bug.fix` | Applies the remediation from the assessment. | `.cairn/workflow/bugs/<slug>/fix.md` |
| `cairn.bug.test` | Validates the fix and records the verification report. | `.cairn/workflow/bugs/<slug>/test.md` |

## Slug Conventions

A *slug* is the per-bug directory name under `.cairn/workflow/bugs/`. It is the only handle the three commands share.

- **User-provided**: any shape the user wants, normalized to lowercase kebab-case (e.g. `login-timeout`, `cve-2026-001`, `oauth-redirect-500`). The slug is preserved verbatim after normalization — no timestamps or numbers are appended automatically.
- **Asked for**: in interactive use, `cairn.bug.assess` asks for a slug when none is supplied, suggesting a kebab-case default derived from the bug summary.
- **Automated**: when no human is available to answer, the agent generates a slug itself. The generated slug **MUST** produce a unique directory — if `.cairn/workflow/bugs/<slug>/` already exists, the agent appends the shortest disambiguating suffix needed (`-2`, `-3`, …) or a short date (`-20260605`). Existing bug directories are never overwritten.

## Installation

```bash
# Install the bundled bug extension (no network required)
cairn spec extension add bug
```

## Disabling

```bash
# Disable the bug extension
cairn spec extension disable bug

# Re-enable it
cairn spec extension enable bug
```

## Typical Flow

```bash
# 1. Triage a bug from a pasted stack trace
/cairn.bug.assess "TypeError: cannot read properties of undefined (reading 'token') at /auth/callback"

# 2. Triage a bug from a GitHub issue URL
/cairn.bug.assess https://github.com/example/repo/issues/1234 slug=callback-token

# 3. Apply the proposed fix
/cairn.bug.fix slug=callback-token

# 4. Validate the fix
/cairn.bug.test slug=callback-token
```

## Guardrails

- `cairn.bug.assess` and `cairn.bug.test` **never modify source code**. They read the repository and write only inside `.cairn/workflow/bugs/<slug>/`.
- `cairn.bug.fix` is the only command that edits source code, and it stays within the files listed in the assessment unless new evidence requires expanding scope (which is logged in `fix.md` under **Deviations from Assessment**).
- None of the commands overwrite an existing report file without explicit confirmation; in automated mode they refuse and pick a new unique slug instead.
- Verdicts and verification results are never over-claimed: a reproduction that was not actually performed is reported as `partial` or `not-run`, not `verified`.

## Hooks

This extension registers no hooks. The three commands are always invoked explicitly by the user.
