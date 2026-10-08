# Implementation Plan: System extraction, linking and the containers view

**Branch**: `004-system-extraction` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md) | **Umbrella**: [002 plan](../002-system-model/plan.md)

## Summary

Design decisions, constitution check, data model and research are shared and live in the umbrella
([plan](../002-system-model/plan.md), [data model](../002-system-model/data-model.md),
[research](../002-system-model/research.md), [frameworks](../002-system-model/frameworks.md)).
This plan states only this slice's scope and state.

- **Size**: L: mostly written; about 4 to 6 days left
- **Depends on**: 002 foundation (model, store migrations). Optional: 003 for `--svg` output.
- **Already written**: Deploy units, routes for nine frameworks, outbound HTTP, messaging, stores, config names, catalog as data, linking with ambiguity, system.yaml additions, sync step, `cairn system --view`, three fixtures, 179 tests (commit 98853f1).
- **Left**: T021 integrate the existing cross-repo call and type passes; T022 `--svg`; T002 a second real product nobody has looked at, for an unbiased SC-001; T045 layout and memory budgets and the 5k-file 20-repo set; T046 Windows CI.

## Constitution Check

Inherits the umbrella's check (all principles pass after the 1.1.1 amendment).
