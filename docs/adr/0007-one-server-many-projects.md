# ADR-0007: One server, many projects, teams and roles

**Status:** Accepted · **Date:** 2026-09-25

## Context
A developer works in several repositories; a team shares memory, specs and history across people and machines.
A server per repository means a process and a port per repository and nothing shared.

## Decision
One server serves every project. Locally it runs on loopback with no sign-in and serves every repository
registered on the machine (`cairn ui` in any repository adds it). In team mode (`cairn serve --team`) it
requires a session or an API token; users belong to teams with roles owner, admin, member and viewer, with
per-project overrides; tokens are scoped and hashed; git projects are cloned and re-synced by push webhooks;
every change is audited. Agents on any machine use a team server through `/mcp/<project id>` with a bearer
token (`cairn agents connect`), and get read-only tools when their role cannot write.

## Consequences
- Same product for one developer and for a team; moving to a shared server changes configuration, not code.
- Platform state (users, teams, projects, tokens) lives in `$CAIRN_HOME/platform.db`, separate from project data.
