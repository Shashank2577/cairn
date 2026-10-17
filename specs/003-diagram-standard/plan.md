# Implementation Plan: Diagram standard: checker, renderer and Mermaid

**Branch**: `003-diagram-standard` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md) | **Umbrella**: [002 plan](../002-system-model/plan.md)

## Summary

Design decisions, constitution check, data model and research are shared and live in the umbrella
([plan](../002-system-model/plan.md), [data model](../002-system-model/data-model.md),
[research](../002-system-model/research.md), [frameworks](../002-system-model/frameworks.md)).
This plan states only this slice's scope and state.

- **Size**: M: mostly written; about 2 to 3 days left
- **Depends on**: 002 foundation (tokens, safe YAML, limits). Nothing else.
- **Already written**: Checker R1 to R13 with evidence resolution, layout with self-test, SVG light and dark, evidence tables, Mermaid export and import (flowchart, sequenceDiagram, C4), `cairn diagram` commands, 111 tests (commit fe23de4).
- **Left**: T024 tokens and geometry endpoint; T026 docs regeneration beyond tokens; T050 render exported Mermaid in CI.

## Constitution Check

Inherits the umbrella's check (all principles pass after the 1.1.1 amendment).
