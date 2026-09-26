# Coding Agent Context Extension

This bundled extension manages the **coding agent context/instruction file** (e.g. `CLAUDE.md`, `.github/copilot-instructions.md`, `AGENTS.md`, `GEMINI.md`, …) for the active integration.

It owns the lifecycle of the managed section delimited by the configurable start/end markers (defaults: `<!-- CAIRN WORKFLOW START -->` / `<!-- CAIRN WORKFLOW END -->`). For `.mdc` files, it also ensures the YAML frontmatter (the metadata block at the top of the file) contains `alwaysApply: true`. Otherwise, everything outside the managed section is untouched.

> NOTE: Cairn itself never touches your agent context file. This extension is the only thing that does, and it's opt-in: install it if you want the block kept in sync, skip it if you'd rather manage that file yourself.

## Why an extension?

Not every Cairn user wants Cairn to write into the coding agent's context file. Keeping this behavior in a dedicated, **opt-in** extension lets users:

- **Choose whether to install it at all** - `cairn spec init` does **not** install it. Add it explicitly when you want Cairn to manage the agent context file; when it is absent, the file is never modified, and when it is disabled, its automatic hooks do not run.
- **Customize the markers** by editing `.cairn/workflow/extensions/agent-context/agent-context-config.yml` ([agent-context-config.yml](./agent-context-config.yml) in this repo) - the bundled scripts honor the `context_markers` value.
- **Synchronize multiple agent anchors** by setting `context_files` when a project intentionally uses more than one coding agent context file, such as `AGENTS.md` and `CLAUDE.md`.
- **Refresh on demand** by running the `cairn.agent-context.update` command in your agent, or automatically through the hooks declared in [extension.yml](./extension.yml) (`after_specify`, `after_plan`).

## Installation

To install the extension, from the root of an initialized Cairn project, run:

```bash
cairn spec extension add agent-context
```

## Disabling

```bash
cairn spec extension disable agent-context

# Re-enable it
cairn spec extension enable agent-context
```

While this extension is disabled (or not installed), nothing in Cairn creates, updates, or removes the managed block - the `__CONTEXT_FILE__` placeholder in any template is left as-is, and the extension's own config is never read.

## Commands

| Command                        | Description                                                                       |
| ------------------------------ | --------------------------------------------------------------------------------- |
| `cairn.agent-context.update` | Refresh the managed section in the agent context file with the current plan path. |

> NOTE: The command ID above is canonical. Invoke it using the syntax for your integration: `/cairn.agent-context.update` for dot-command integrations; `/cairn-agent-context-update` for hyphen/skills integrations (including Forge and Cline); `$cairn-agent-context-update` for Codex or ZCode in skills mode; or `/skill:cairn-agent-context-update` for Kimi.

## Configuration

All configuration flows through the extension's own config file at `.cairn/workflow/extensions/agent-context/agent-context-config.yml` ([agent-context-config.yml](./agent-context-config.yml) in the repo).

## Requirements

The bundled update scripts require **Python 3** with **PyYAML** for YAML/upsert processing (PowerShell can also use `ConvertFrom-Yaml` when available).

PyYAML ships with the `cairn` CLI and is normally available via the same `python3` interpreter. If a hook reports _"PyYAML is required … not available in the current Python environment"_, it means the system `python3` differs from the one used to install Cairn. To resolve, run:

```bash
pip install pyyaml
# or target the specific interpreter Cairn uses:
/path/to/cairn-python -m pip install pyyaml
```

## Issues

For any other issues, please create an issue in the [official GitHub repo](https://github.com/Shashank2577/cairn/issues).
