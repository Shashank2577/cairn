# Implementation Plan: System extraction, linking and the containers view

**Branch**: `004-system-extraction` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md) | **Umbrella**: [002 plan](../002-system-model/plan.md)

## Summary

Design decisions, constitution check, data model and research are shared and live in the umbrella
([plan](../002-system-model/plan.md), [data model](../002-system-model/data-model.md),
[research](../002-system-model/research.md), [frameworks](../002-system-model/frameworks.md)).
This plan states only this slice's scope and state.

- **Size**: L: mostly written; about 2 to 3 days left
- **Depends on**: 002 foundation (model, store migrations). Optional: 003 for `--svg` output.
- **Already written**: Deploy units, routes for nine frameworks, outbound HTTP, messaging, stores, config names, catalog as data, linking with ambiguity, system.yaml additions, sync step, `cairn system --view`, three fixtures, 179 tests (commit 98853f1). The robot-shop blind fixes T052 to T057 (2026-10-13): nginx `proxies` rules, PHP support with curl and PDO, env-fallback resolution, build-context stores, inferred outside systems from configured domains, soft view budgets with member-carrying collapse; robot-shop re-scored 1.00/0.86 elements, 1.00/1.00 relationships (no longer blind — see blind-accuracy.md). The third product, online-boutique (2026-10-15, T002): blind 0.92/0.79 and 0.00/0.00 (all gRPC), then T058 (gRPC client rules + configured-address wiring) and T059 (image-with-source attribution, ASP.NET Redis) took it to 0.92/0.86 and 0.94/0.84 — also no longer blind; the next unbiased round needs a fourth product (GraphQL or a service mesh would measure the two unmeasured call styles).
- **Left**: T021 integrate the existing cross-repo call and type passes; T022 `--svg`; T045 layout and memory budgets and the 5k-file 20-repo set; T046 Windows CI; a PHP inbound-route framework rule when a second PHP product appears.

## Constitution Check

Inherits the umbrella's check (all principles pass after the 1.1.1 amendment).
