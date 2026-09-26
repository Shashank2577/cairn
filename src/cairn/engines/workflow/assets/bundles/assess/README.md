# Idea Assessment Bundle

A first-party Cairn bundle that installs an idea-triage pipeline before committing to Spec-Driven Development.

## What it provides

- **Assess extension** (`extensions/assess`) — the `cairn.assess.intake`, `cairn.assess.research`, `cairn.assess.define`, `cairn.assess.shape`, and `cairn.assess.decide` commands.
- **Assess workflow** (`workflows/assess`) — a guided, resumable pipeline:
  1. `intake` the raw idea.
  2. `research` the evidence.
  3. `define` the problem.
  4. `shape` the concept.
  5. `decide` the verdict.
  6. `review-verdict` gate — approve to complete the assessment; reject to abort. A `go` verdict is then handed off manually to `/cairn.specify`.

## Install

```bash
cairn spec bundle install assess
# or
cairn spec bundle add assess
```

## Run the workflow

```bash
cairn spec workflow run assess \
  --input idea="Let users work offline and sync when they reconnect" \
  --input slug="offline-mode"
```

Required inputs must be supplied with `--input`: `idea` and `slug`. The slug is used as the working directory under `.cairn/workflow/assessments/<slug>/` for all artifacts.

## Remove

```bash
cairn spec bundle remove assess
```

Removing the bundle uninstalls the workflow and the extension it contributed, unless they are still depended on by another installed bundle (FR-022). Components you installed independently are not attributed to this bundle and survive removal.
