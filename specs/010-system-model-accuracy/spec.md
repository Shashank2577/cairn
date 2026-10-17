# Feature Specification: System view accuracy on robot-shop

**Feature Branch**: `010-system-model-accuracy`  **Created**: 2026-10-16  **Status**: Implemented

**Input**: The robot-shop documentation experiment (instana/robot-shop at 55292e2199f2fb00a165b1f7d3045fe7f8922038,
PR #1 head 1f1c7fd). Agents using `cairn system --view containers` hit these concrete errors. Ground truth is the
repository's code and `tests/system/fixtures/robot-shop/GROUND_TRUTH.yaml`.

## Requirements

- **FR-001** A relationship's protocol comes only from client call sites. With none (configured-address wiring) no
  protocol is claimed. An empty set of calls is never "all gRPC".
- **FR-002** When a relationship's target comes from a configured address, evidence cites the line that uses the
  address (the request that builds on it), not the line that names the host. Call-site evidence comes before
  configuration evidence.
- **FR-003** A container's technology comes from the real runtime: a language base image beats an entry
  script; a server or daemon image (nginx, Fluentd, ...) is the technology when only a wrapper script is left.
  An nginx container that exposes a port and serves static files is a web app, not a job.
- **FR-004** A relationship summarising several distinct calls lists them (`GET /check/{}, POST /order/{}`) when
  they share no path prefix and fit the label budget; a configured default adds no path beside real calls.

## Success criteria

- **SC-001** The four errors above are gone on the pinned robot-shop (`tests/system/test_robot_shop_gaps.py`).
- **SC-002** The robot-shop and online-boutique regression floors still pass unchanged.

## Out of scope

Load generators that call through a framework client (Locust `self.client.get('/api/...')`) are not extracted
as calls: `load -> web` stays a configured-address edge with no protocol and no path.
