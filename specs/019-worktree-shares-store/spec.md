# 019 — A worktree never grows a second store

## Problem
Agents, hooks and session capture in a linked git worktree already use the main checkout's store when the
worktree has none. The CLI did not: `Project.discover()` found the worktree's committed `.cairn/config.toml`, and
any command run there (`cairn`, `status`, `sync`, `impact`, `init`) created a separate `brain.db`. From then on
everything in that worktree used the separate store, with its own memory and its own model spend.

Seen on 2026-11-20: the Orca worktree `spreix-notetaker/deploy-stage-2` had its own store since 22 October
(29 memories against 146 in the main checkout; 4,805 model calls of its own). Reproduced in a scratch repository:
`cairn status`, `cairn sync`, `cairn init` and bare `cairn` each created a store in a new worktree.

## Change
- `project.main_checkout_project(root)`: the main checkout's set-up project for a linked worktree (shared with
  `agent_project`).
- `cli._discover()`: this checkout's project, or the main checkout's when this is a worktree with no store of its
  own; it says which on stderr. Used by every command that opens a store, bare `cairn` and `init`.
- `cairn init` in such a worktree reports that it shares the main checkout's store and sets nothing up.

## Not changed
A worktree that already has its own store keeps it: moving or merging an existing store could strand data.
