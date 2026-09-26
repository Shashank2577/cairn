"""Recall: Cairn's agent session memory.

Agents' own hooks record what happens in a session (prompts, tool calls, the final answer) into a
durable queue in ``<repo>/.cairn/sessions.db``. A worker turns that queue into *observations*
(typed, titled records with facts, a narrative, concepts and the files read/changed) and
per-prompt *session summaries* (request / investigated / learned / completed / next steps), written
by a model through ``cairn.router.Router`` or, when no model is available, derived deterministically.
Everything is searchable (FTS5 + local vectors), injected back into new sessions as a compact
timeline, exposed as progressive-disclosure tools (search -> timeline -> get_observations) and
streamed to the UI.

Module map (the hook path is standard library only, so hooks stay fast):
  schema, store, tags, settings, projects, platforms, modes, prompts, parser, fmt,
  context, adapters, ingest, hooks, transcript_parser, health     -- stdlib (hook path)
  observer, fallback, worker, search, vectorsync, mcp, viewer, cli,
  transcripts, smartread, folders, integrations, corpus, worktrees -- worker / server side

Nothing is imported here on purpose: ``python -m cairn.capture`` must not pay for the worker.
"""
