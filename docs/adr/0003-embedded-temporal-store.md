# ADR-0003: Embedded temporal store by default

**Status:** Accepted · **Date:** 2026-09-24

## Context
The temporal fact graph supports Neo4j, FalkorDB, Neptune and an embedded driver. Requiring a
server (usually Docker) violates the one-command principle. The embedded driver is marked
deprecated upstream.

## Decision
Default to the embedded store in `.cairn/timeline.kuzu`. Setting `deep.graph_url` to `falkor://`
or `bolt://` switches to a server with no other change. Facts are always mirrored into the read
model, so surfaces never depend on the timeline store's availability.

## Consequences
Zero-setup deep tier today. When the embedded driver is removed upstream, the default moves to
FalkorDB (Lite or server) with a migration note; mirrored facts keep working in the meantime.
