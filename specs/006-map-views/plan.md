# Implementation Plan: Map views: Context, Containers, Components, Flows, Code, and dependency layers

**Branch**: `006-map-views` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md) | **Umbrella**: [002 plan](../002-system-model/plan.md)

## Summary

Design decisions, constitution check, data model and research are shared and live in the umbrella
([plan](../002-system-model/plan.md), [data model](../002-system-model/data-model.md),
[research](../002-system-model/research.md), [frameworks](../002-system-model/frameworks.md)).
This plan states only this slice's scope and state.

- **Size**: L: about 5 to 7 days
- **Depends on**: 003 (renderer and tokens) and 004 (model and views).
- **Already written**: Dependency layers redesign, partial: vendored hidden, cycles collapsed, entry points, before/after screenshots (commit b6ae4ea).
- **Left**: Map tabs and c4view component; remove Engine views; components and flows; finish the layers budget and move it into Code; UI tests; the moderated new-joiner study.

## Constitution Check

Inherits the umbrella's check (all principles pass after the 1.1.1 amendment).
