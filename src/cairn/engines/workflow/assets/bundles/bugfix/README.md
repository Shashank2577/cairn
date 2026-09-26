# Bug Fix Bundle

A first-party Cairn bundle that installs an orchestrated bug-fixing pipeline.

## What it provides

- **Bug extension** (`extensions/bug`) — the `cairn.bug.assess`, `cairn.bug.fix`, and `cairn.bug.test` commands.
- **Bugfix workflow** (`workflows/bugfix`) — a guided, resumable pipeline:
  1. `assess` the bug report.
  2. `review-assessment` gate — approve to proceed, reject to abort.
  3. `fix` the bug.
  4. `test` the fix.

## Install

```bash
cairn spec bundle install bugfix
# or
cairn spec bundle add bugfix
```

## Run the workflow

```bash
cairn spec workflow run bugfix \
  --input report="https://github.com/example/repo/issues/1234" \
  --input slug="callback-token"
```

Required inputs must be supplied with `--input`: `report` and `slug`. The slug is used as the working directory under `.cairn/workflow/bugs/<slug>/` for all artifacts.

## Remove

```bash
cairn spec bundle remove bugfix
```

Removing the bundle uninstalls the workflow and the extension it contributed, unless they are still depended on by another installed bundle (FR-022). Components you installed independently are not attributed to this bundle and survive removal.
