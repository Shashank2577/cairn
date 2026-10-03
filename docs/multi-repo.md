# Many repositories, one team

Cairn is built for one server serving many repositories ([ADR-0007](adr/0007-one-server-many-projects.md)).
This page is the deployment story for an organisation — say, a 12-engineer team working across 8
repositories — across three tiers of memory, two hosting paths and the scale and security questions
that come with each. The mechanics of team mode are in [teams](teams.md); the architecture is in
[architecture](architecture.md).

## The three tiers

| Tier | Where it lives | Who writes | Who reads |
|---|---|---|---|
| Local brain | `.cairn/` in each engineer's checkout of each repo | That engineer's syncs, capture hooks and memory seeds (their own model plan) | That engineer and their agents, offline |
| Team server | `$CAIRN_HOME` on one server: `platform.db`, per-project clones under `repos/`, each clone's own `.cairn/` | The server's syncs (push webhooks), agents' pushed sessions, memories written through the API | Every member, per role; every agent with a token |
| Git layer | Committed files: `.cairn/config.toml`, `workflow/`, specs, ADRs — reviewed by PR like any code | Anyone, through a PR | Everyone who clones, including the server when it registers a repo |

The tiers are layers, not alternatives. A new engineer gets value with no server at all: clone, run
`cairn sync`, and the local brain answers from git history, the map and seeded memory. Join the team
server and the same questions gain everything the team knows. What a lesson should be, is decided by
where it belongs: a personal gotcha stays local, a convention is written into the repo's committed
config and reviewed by PR, and everything in between lands on the server as a memory.

```text
  12 engineer laptops                                one team server (cairn serve --team)
 +----------------------------+                     +--------------------------------------+
 | agent harness (Claude Code,|   Bearer token,     |  auth: sign-in, roles, API tokens    |
 | Codex, Cursor, ...)        |   HTTPS             |  project registry: 8 repos           |
 |   |  stdio                 | --------------------|  shared memories, sessions, timeline |
 |   v                        |  /mcp/<project>/    |  audit log                           |
 | local .cairn/ per repo     |  sessions push      |  +--------------------------------+  |
 |  brain.db, sessions.db,    | --------------------|  | clones: repos/<project id>/    |  |
 |  memstore/, graph/         |  (cleaned, outbox,  |  |   .cairn/ brain per project    |  |
 +----------------------------+   idempotent)      |  +--------------------------------+  |
        |          ^                               +------------------|-------------------+
        |          |                                                  |
        | sync     | hooks                                            | reads/writes
        v          |                                                  v
   git forge ------------------- push webhook -----------------> +---------------------------+
   (GitHub/Gitea/...)                                            | shared stores (optional)  |
        |                                                        |  embedded SQLite default; |
        | PRs: committed .cairn/config.toml, workflow/, specs    |  Qdrant / Neo4j extras    |
        v                                                        +---------------------------+
   reviewed conventions & lessons  ---- cloned by the server --->  (part of the git layer)
```

Nothing in the local brain leaves the laptop unless a member connects it: session capture is queued
in an outbox, cleaned (private blocks removed, secrets masked) and pushed under the member's own
token, so each session belongs to the member whose token sent it ([teams](teams.md#session-capture-from-other-machines)).

## The system layer

One server keeps one registry of projects, so cross-repo questions are registry questions:

- **Per-project maps.** Every project — a clone on the server or a checkout on a laptop — builds its
  own map (`graph.json`). The map knows which packages and modules the project declares and which it
  imports.
- **The global map.** `cairn graph global add <graph.json> --as <repo-tag>` merges project maps into
  one cross-repository graph under `$CAIRN_HOME/graph/` (`global-graph.json` plus a manifest). The
  merge links member calls across repos, and `cairn graph merge-graphs a.json b.json …` additionally
  joins identically declared types, so "who calls this?" answers across repository boundaries.
  `cairn graph global list` shows what's in it; `merge-graphs` also produces a one-off merged file
  when you don't want a standing one.
- **The cross-repo endpoint.** `GET /api/repos/map` returns every repository the caller can read and
  the imports that link them, built from the maps that already exist — never rebuilt on request.

A change in one repo therefore surfaces in another: the merged graph links the caller's node to the
callee's repo, and the cross-repo map names the repositories that declare what this repo imports.

```mermaid
sequenceDiagram
    participant D as Developer
    participant F as Git forge
    participant S as Team server
    participant R as Reviewer
    participant M as Memory

    D->>F: open PR, push commits
    F->>S: push webhook (HMAC-signed)
    S->>S: refresh clone, re-sync the project
    D->>R: requests review
    R->>S: review context: impact for the changed files<br/>(cairn why / impact on the page or via MCP)
    S-->>R: local impact + cross-repo hits<br/>(who imports/calls this, from the global map)
    Note over R: today the join is imports, calls and<br/>types in the merged graph; parsing<br/>ticket ids from commit trailers is<br/>planned, not built
    R->>M: confirms the lesson (cairn remember,<br/>or the MCP write tools)
    M->>S: durable memory on the team brain,<br/>linked to the files it mentions
```

The planned CLI surface for the review step is a single command that assembles the pack — ticket,
cross-repo hits and local impact in one call — and a `system.yaml` that names which repositories
form one product. Neither exists yet; the pieces above are what works today, and they already carry
the flow end to end.

## Hosting: self-hosted path

The default. One small server, one binary, no infrastructure dependencies unless you add them.

**Hardware.** The server is one deterministic FastAPI process (uvicorn) over SQLite
([architecture](architecture.md#server-and-hub)). Queries are dictionary and index lookups; answers
never need a model. For a 12-engineer, 8-repo organisation:

| Resource | Sizing | Why |
|---|---|---|
| CPU | 2 vCPU | Map builds are tree-sitter passes; memory reconciliation runs at most `seed_model_limit` (25) candidates per sync |
| RAM | 4 GB | SQLite, one project's map in memory at a time, SSE fan-out for 12 browsers |
| Disk | under 20 GB in the first year, including session capture | 8 clones with per-project `.cairn/` brains, `platform.db`, the embedding model cache |
| Network | outbound HTTPS for clones, webhooks in | The first sync of each project downloads the embedding model once, to `$CAIRN_HOME/models` |

A $5-10/month VPS (2 vCPU / 4 GB) carries this. Run `cairn serve --team` on loopback behind a TLS
reverse proxy ([teams](teams.md#deploying-behind-a-reverse-proxy)); `deploy/` in the repository has
a Dockerfile and compose file for exactly this shape.

**Backups.** One nightly job copying `$CAIRN_HOME`: `platform.db`, `secret.key` (they back each
other up — the key derives webhook secrets and signs CSRF tokens), `server.toml`, and the per-project
clones whose `.cairn/` holds the team brain. Everything except the clones and the platform state is
rebuildable with `cairn sync`.

**Model-token economics.** Most model work never touches the company's bill:

- **Local brains run on each engineer's own plan.** Sync, capture and memory seeding on a laptop
  spend that engineer's tokens, and a repository that hasn't changed costs nothing on the next sync
  ([models and cost](models-and-cost.md)).
- **The server's own spend is bounded by design.** Syncs run under a budget and every call lands in
  the ledger; memory seeding reconciles at most 25 new candidates per run; timeline facts stop at
  `[deep] budget_tokens` (150,000 default). Session derivation, once engineers connect to the team
  server, happens once on the server instead of per laptop (`[recall] worker_model = false` is set
  by `cairn agents connect`).
- **Answers are deterministic.** Context packs are assembled and fitted to a budget without a model;
  narration (`--explain`, `ask`) is the only model call, and an answer's pack is measured in
  hundreds of tokens.

Measured in our own dogfood: seeding one repository costs about 100k tokens, once; heavy capture
runs about 5-8M tokens a week per engineer, on their own plan; a typical answer costs 0.5-1k tokens
to narrate. For a 12-engineer org the marginal company spend is the server's provider key, and it is
small next to any engineer's weekly budget.

## Hosting: SaaS path

The same image, offered as a subscription. This is a design summary, not a shipped product; it's
here so the self-hosted path and the SaaS path make the same promises.

**What already fits.** One server already serves many teams and projects: the platform holds users,
teams, roles, tokens, projects and the audit log, and team creation is already gated
(`team_creation = "admins"`). The memory engine's shared stores already separate per project — a
SaaS deployment points `vector_store` at managed Qdrant or pgvector (the `memory-server` extra) and
`[temporal] url` at managed Neo4j. Facts and memories stay mirrored into each project's read model,
so a tenant keeps answering while a shared store is down ([ADR-0003](adr/0003-embedded-temporal-store.md)).

**What must be built for SaaS that self-hosting does not need.** An honest list:

- **Platform database beyond SQLite.** `platform.db` is one SQLite file per server. Many
  tenants on one server wants a network database with real concurrency and point-in-time recovery.
  The pgvector client libraries ship in the `memory-server` extra today, but they serve the memory
  vector store, not the platform tables — the platform swap itself is unbuilt work.
- **Tenant isolation hardening.** Roles already answer 404 vs 403 and authorisation is enforced in
  the service layer, but multi-tenancy raises the stakes: per-tenant encryption keys, rate limiting
  per tenant, and an audit of every cross-tenant code path (git clone credentials, webhook secrets,
  model keys — operator-level settings today, per-tenant settings then).
- **Object storage for brains.** Per-project `.cairn/` folders live on the server's disk today. A
  SaaS build moves clones and brains to object storage with lifecycle rules; nothing in the read
  model's design prevents this, but it is unbuilt.
- **Billing and metering.** Per-seat billing at the platform layer: seats map cleanly onto users,
  and the `ledger` and `queries` tables already meter model tokens and served packs per project.
- **ToS, privacy pages, data export and deletion.** Session capture holds conversation content; a
  hosted product needs terms for that, a documented retention story and a working delete.

Self-hosting needs none of this: one org trusts one operator, and the platform is already scoped to
one team's data.

## Scale path

The embedded stores are the default and the right default. Move off them when a measure, not a
hunch, says so:

| Signal | Swap | How |
|---|---|---|
| Memory recall slows as many projects share one server, or a second server needs the same memories | Qdrant, pgvector or Chroma | `[memory] vector_store` in the project's committed `.cairn/config.toml`; `cairn-brain[memory-server]` |
| Fact graphs grow past what the embedded Kuzu file handles comfortably, or several servers share facts | Neo4j, FalkorDB (Neptune works too, client libraries not bundled) | `[temporal] url`; `cairn-brain[temporal-neo4j]` or `[temporal-falkordb]` |
| One server can't hold every clone | A second server for another set of projects | Nothing to build: each server is independent; point `[memory]`/`[temporal]` at shared stores if they must share |

Facts and memories are always mirrored into each project's read model, so the page and agents keep
answering from the mirror while a shared store is down. What's on the roadmap, not shipped: the
platform database swap and object-storage brains described in the SaaS section, and the
`system.yaml` grouping of repositories into one product.

## Security

- **Tokens per engineer.** Every agent and script gets its own token, acting as its user within one
  team and optionally one project, narrowed further by scope (`read`, `agent`, `ci`, `all`). Only a
  SHA-256 digest is stored; tokens expire (90 days by default) and revoking a member revokes their
  tokens. A token can never manage passwords, sessions or other tokens.
- **TLS at the proxy.** `cairn serve --team` binds loopback; the reverse proxy terminates TLS.
  `public_url` pins the allowed Host header and browser origin, `trust_proxy` turns on
  `X-Forwarded-*` handling, session cookies are `HttpOnly`/`SameSite=Lax` (Secure with the `__Host-`
  prefix over HTTPS), and state-changing cookie requests need the CSRF token header.
- **Local brains stay local.** `.cairn/`'s gitignore keeps everything local except the committed
  config and `workflow/`; brains, sessions, maps and memory stores are never pushed. Pushed sessions
  are cleaned before they leave the laptop: private blocks removed, secrets masked, long fields
  trimmed — and each session is owned by the member whose token pushed it, with an id prefixed by
  their user id so one member can't write into another's sessions.
- **The audit log exists.** Every platform change — sign-ins including failed ones, role changes,
  token issue and revocation, project registration, webhook deliveries — is recorded with actor,
  target, time and client address, readable per team (`team.audit`) and in full by server admins.
- **The server never takes credentials from a repository.** Model keys and shared-store settings
  are operator-level: `$CAIRN_HOME/server.toml` or `CAIRN_*` environment variables, never a pushed
  `config.toml`, so a commit can't exfiltrate the server's keys or redirect its stores.
