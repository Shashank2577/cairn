# Data Model: System Model

All tables live in the repository's own `.cairn/brain.db`. Sibling repositories are read, never written.

## Element (`sm_element`)

| Field | Notes |
|---|---|
| id | stable: `repo:type:key` (for example `shop-api:container:shop-api`, `shop-api:route:POST /api/orders`) |
| level | landscape, context, container, component, code |
| type | person, system, external-system, container, data-store, channel, library, component, code, entity, state |
| kind | container kind tag (web-app, service, worker, cli, job, function, …) |
| name, tech, desc | desc is derived; narrative may add `desc_narrated` (labelled inferred) |
| parent | container for a component; system for a container |
| provenance | extracted, declared, inferred, ambiguous, stale |
| stale_reason, stale_since | set by re-check |
| built_at_commit | commit the element was computed at (FR-029c) |

## Relationship (`sm_relationship`)

| Field | Notes |
|---|---|
| id | stable hash of (from, to, rule, key) |
| from_id, to_id, level | |
| what, how | label parts; "how" null at context level |
| style | sync, async, build |
| rule | how it was found: http-route-match, pubsub-topic, store-use, catalog-sdk, package-dep, declared, cross-repo-call, shared-type |
| provenance, stale_reason, stale_since | |
| data_class | optional overlay: public, internal, personal, sensitive |

## Evidence (`sm_evidence`)

| Field | Notes |
|---|---|
| claim_id | element or relationship id |
| repo, file, line | or `entry` for manifest and deploy entries (`docker-compose.yml: services.db`) |
| kind | route, call, publish, subscribe, driver, image, script, dockerfile, manifest, declared |
| commit_confirmed | the commit at which the evidence was last re-derived |

## Signal facts (inputs, per repository)

Routes (method, path template, handler, location), outbound calls (method, path template, base-config
name, location), channels (client, topic, publish or subscribe, location), store uses (driver, operation,
table, location), deploy units (Dockerfile, compose service, Kubernetes workload: ports, command, image, env
names), entry points (script, bin, main), config names (name, where read).

## Flow (`sm_flow`, `sm_flow_step`)

Flow: id, entry element, title, provenance. Step: flow id, index, relationship id or call edge, what, how,
style, evidence.

## Rules

- Values from configuration and deploy files are never stored; names are, except names the catalog marks
  sensitive, which are kept as their kind only. A service URL that resolves to a sibling stores the sibling's
  identifier, never the URL or host string.
- A claim no longer derived is marked stale (not deleted) and becomes a `stale-architecture` drift finding.
