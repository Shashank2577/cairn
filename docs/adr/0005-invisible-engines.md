# ADR-0005: One product, invisible engines

**Status:** Accepted · **Date:** 2026-09-24

## Context
Cairn composes five open-source engines. Exposing each one's vocabulary, commands and folders
would make users learn five tools.

## Decision
Users and agents see five layers (Map, Specs, Timeline, Memory, Sessions), one CLI, eight MCP
tools and one page. Engine names never appear in commands, UI copy or tool names. Adapters in
`cairn/engines/` are the only place engine APIs are called. The map engine's own agent skill is
not installed; agents use Cairn's MCP. Licence notices are kept in `THIRD_PARTY_NOTICES.md` as the
licences require.

## Exceptions, stated honestly
The spec workflow's own slash commands (`/speckit-*`) and folders (`.specify/`, `specs/`) stay as
they are: they are the workflow users run, and renaming them would break upstream updates. The map
engine writes to `graphify-out/`. Session capture shows its own messages inside Claude Code.
