# ADR-0006: Session memory is built in

**Status:** Accepted · **Date:** 2026-09-25

## Context
Agents forget everything between sessions. Remembering what they read, changed, ran and learned, and handing
it back to the next session, is one of the most valuable things Cairn does, and it must work on any machine
with Python and an agent.

## Decision
`cairn init` adds hooks to the repository's agent settings. The hooks are tiny, standard-library entry points
that only append events to `.cairn/sessions.db`, so an agent is never slowed down or broken. A worker turns
queued events into observations (type, title, narrative, facts, concepts, files) and session summaries
(request, investigated, learned, completed, next steps) through the model layer, or into deterministic
observations when no model is available. Observations are searchable (full text and semantic), exposed to
agents as search → timeline → full-detail tools, injected at session start within a token budget, and linked
to the files they touched. Content inside `<private>` tags and credential-like values are never stored.

## Consequences
- The next agent asking about a file gets the earlier sessions that touched it.
- Session data lives with the repository and can be pushed to a team server.
