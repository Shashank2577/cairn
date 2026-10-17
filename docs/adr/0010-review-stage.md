# ADR 0010: Review is a Cairn-native stage

Status: accepted (2026-10-17). Spec: `specs/013-cairn-review`.

- **Native pipeline.** Review is written in Python inside Cairn, following a bundle, rule-match, review, reflect shape.
  No per-platform binary and no extra install step (constitution III).
- **Deterministic first.** The stage that needs no model (missing co-changes, drift, history, conventions, secrets,
  tests, dependency locks) is complete on its own and works with zero keys (constitution II).
- **Every finding is cited.** A finding without provenance (`EXTRACTED` or `INFERRED`) and at least one citation is rejected.
- **Tool count.** The agent tool `cairn_review` becomes the 13th core tool when it ships (ADR 0008).
- **Decisions with the owner.** Posting to the forge is on by default (one updating summary, per-project off switch);
  CI fails on high severity by default; the pull request's author pays for automatic model reviews, and without a
  user or model the review is deterministic only.
- **Numbering.** The spec called this ADR 0009; that number was taken by the route-extraction ADR.
