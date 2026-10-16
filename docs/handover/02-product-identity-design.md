# Design document — making a product a first-class thing in Cairn

**Status:** Proposal, not started · **Written:** 2026-10-10 · **For:** review by a stronger model, then implementation
**Companion:** [01-session-changes.md](01-session-changes.md)

This document exists so another AI can evaluate the design without reading the repo first. It states
the problem, the constraint that makes it hard, three candidate designs with their costs, a
recommendation, and the questions that must be answered before implementation.

---

## 1. The question

> Select the product in the UI. Every view inside it describes the product, not one repository.
> Repositories become something you move *within*, not the thing you are.

Today, selecting "ASG Edge+" navigates to the System tab of whichever repository owns
`system.yaml`. That repository is then the project for every other tab. There is no way to be *in*
the product.

---

## 2. The constraint that makes this hard

Cairn's identity is `pid`, and it is load-bearing everywhere.

| Surface | Shape |
|---|---|
| Routes | `/p/{pid}/map`, `/p/{pid}/specs`, `/p/{pid}/memory`, … |
| Registry | `projects` table, `project_id` on nearly every table |
| Stores | one `brain.db`, `sessions.db`, `memstore/` per repository |
| Graph edges | `links.src`, `links.dst` are node ids, implicitly repo-scoped |
| Specs | `specs/` directory inside the repository |
| Memory | `memories` per project; seeds are per-repo |
| Hooks | one `.cairn/config.toml` per repository |
| Sessions | captured per repository |

A product declared by `system.yaml` has **no row anywhere**. It is a file *inside one repository* that
points at its siblings. Consequences that are already load-bearing in the code:

- Product membership only resolves for registered roots. Add a repo to `system.yaml` without
  registering it and the count is wrong. This is not a hypothetical — it is the current behaviour.
- `system.yaml` is discovered by walking from one repository's root. A product is only visible while
  that repository is the one you opened.
- The five sibling repositories have independent, divergent stores. There is nothing to point a
  `product_id` at.

**So the real question is not "add a product page". It is: does a product become a first-class project,
or a derived view over several?**

---

## 3. Three candidate designs

### A — Product as its own project

Register `ASG Edge+` as a project. `system.yaml` becomes its configuration. Opening it gives
product-wide Overview, one merged Map, one Specs, one Memory. Repositories become switchable inside
it.

**What it requires**

1. A `products` table, or a project row with a `kind` column, plus a membership join table.
2. `system.yaml` promoted from "a file in a repo" to "the product's own config" — a home outside any
   member repository.
3. A **merged read model.** The expensive part. `links` is currently repo-scoped by construction. A
   merged graph needs a synthetic root that can resolve node ids from five repositories into one
   address space, without id collisions. Options:
   - qualify every id at write time (`<repo>:<path>:<line>`) — a breaking change to every stored
     edge and every stored memory reference;
   - namespace at read time — no migration, but every consumer must learn to qualify;
   - a separate cross-repo index built on demand — cheapest, and another thing that can go stale.
4. Decisions on Specs and Memory: does the product hold them centrally with repos contributing, or
   stay per-repo? **This changes the design more than anything else.**

**Honest cost:** large. Realistically a new spec (009), touching the registry, the router, the graph
read model, memory seeding, and hooks. Weeks, not days.

**Upside:** makes Principle I (*One Product, Invisible Seams*) true rather than aspirational, and
the nine-document deliverable gets a natural home.

### B — Product as a view layer

Keep products derived. Add `/product/{name}` beside `/p/{pid}`. The product route offers System,
merged Explore, a repository picker, and a combined repo list. Specs, Memory, Sessions stay
per-repository, with links between them.

**What it requires**

1. A product route and a resolver (this part is ~done: `repos.py::_product` and
   `server.py::_products_by_project`).
2. A read-only merged Explore. Cheaper than A because it never writes and never unifies ids — it
   queries each repository and lays the results out together.
3. A decision on what "current project" means in a UI where you can be in a product.

**Honest cost:** small — days. **Risk:** it may entrench the split rather than fix it. Two homes for
every concept, and an eventual migration to A that touches both.

### C — Product-scoped read model

A synthesised project that merges all members into one queryable index, generated from the members'
own stores. Conceptually A's read model without A's write model.

**What it requires:** the merge machinery from A.3, plus generation and invalidation. But no new
registry rows and no change to Specs or Memory.

**Honest cost:** medium. **Risk:** a derived index that silently goes stale is worse than no index —
it produces confidently wrong answers. Requires the staleness rules that
`src/cairn/system/recheck.py` already implements for the system model.

---

## 4. Recommendation

**A, staged so B ships first.**

Stage 1 (B, days): the product route, merged Explore, repository picker. This is the part that
immediately answers "where is the product" and needs no architectural commitment.

Stage 2 (C, weeks): the merged read model, proven by whether Stage 1's Explore stays fast and honest
at five repositories.

Stage 3 (A, weeks): only once the merged read model is known to work — promote the product to a
registry row, and decide Specs and Memory ownership with real data in hand.

**Do not start at A.** The registry and read-model changes are irreversible-ish, and the central
question — whether Specs and Memory are per-repo or per-product — has not been answered. Starting at A
commits to an answer before anyone has looked at the consequences.

**Do not stay at B permanently.** Two homes for every concept is the same disease as today, with
better labelling.

---

## 5. What is already built, and what it is worth

Do not rebuild these. All verified working:

| Piece | Location | Note |
|---|---|---|
| `system.yaml` reader | `repos.py::_product` via `systems.discover()` | Reuses the system layer's reader; does not parse YAML itself |
| Cross-project resolution | `repos.py::repos`, `server.py::_products_by_project` | Resolved once across all projects. **See the trap below.** |
| Product in session payload | `server.py` `/api/session` | `{system, members, anchor}` |
| Product grouping in switcher | `shell.js` | Selectable, anchored to the declaring repo |
| Product grouping in repo list | `map.js` `RepoView` | |
| Product banner on System tab | `map.js` `SystemView` | |
| Declared + shared links | `repos.py::_declared_product_links`, `_shared_element_links` | Fallback when import links are empty |

**The trap.** Only the repository owning `system.yaml` has one; the other four return `None`. Every
product lookup must key on resolved member roots. This bug appeared three times in one session:
grouping nothing, reporting `members: 1`, then dropping four of five repos. Recorded in Cairn memory
as `ed3164c38d`. Any new code here must resolve across projects on the first attempt.

**Also built this session, and worth knowing about because it fixes a symptom you reported as
"everything is broken":** `/graph/architecture` took 50.7s and blocked the entire page. Now 1.0s cold,
0.009s warm, via `_map_stamp` + `_architecture_cached`.

---

## 6. Questions to answer before implementing

**Blocking:**

1. **Are Specs and Memory per-repository or per-product?** User-Service has its own
   `specs/AUTH-01…06`. If the product holds specs centrally, what happens to those? If they stay
   per-repo, what does a product-level Specs tab show? *This single answer reshapes A more than
   anything else.*
2. **Does a product own its configuration, or does it still live in a member repository?** Today
   `system.yaml` is inside Audit-Service. Deleting that file silently dissolves a product that five
   repositories depend on. Unacceptable either way — but the fix differs per design.
3. **What happens to a repository that is in two products?** `system.yaml` allows it; the
   membership model may not.

**Non-blocking but should be decided:**

4. **Should merged Explore ever show code-level detail** (symbols, files), or only containers and
   flows? Full merge is significantly harder than a container-level merge.
5. **Does the repository switcher remain?** Under A, probably yes as an in-product control. Under B,
   probably not.
6. **What is the project id of a product?** If `pid` stays the only identifier, a product needs a
   reserved prefix, which collides with the "many projects, one server" model from ADR-0007.

---

## 7. Constraints any design must respect

From the repo's own principles and ADRs, so a proposal that violates them can be rejected on sight:

- **Principle II — Deterministic First, Models Second. Non-negotiable.** Product structure must come
  from `system.yaml` and evidence, never from inference. The current `shares` fallback is already at
  the edge of this; it is acceptable because shared elements are evidence-backed.
- **ADR-0007 — one server, many projects.** A product must not become a second registry competing
  with projects.
- **Principle I — One Product, Invisible Seams.** The target state. A design that makes the seams
  *more* visible is moving backwards.
- **Generated docs are checked.** `scripts/sync_*.py --check` fail CI when stale.
- **Sibling repositories are read-only.** `link.py` documents this. No design may write to a member
  repository from the product.

---

## 8. What I would ask a reviewer to check

1. Is the staged A←B←C sequence right, or should the merged read model be proven before any UI?
2. Is question 1 (specs and memory ownership) really the blocking one, or is there a third question
   of equal weight I have missed?
3. Does B risk entrenching the split badly enough that it should not ship at all?
4. Is the id-namespace problem in A.3 harder than I have described? I have flagged it as the expensive
   part but not sized it.

---

## 9. Reproduction

Every claim is checkable. Commands and expected values are in
[01-session-changes.md §7](01-session-changes.md#7-verifying-this-documents-claims).

Environment requirements: `KMP_DUPLICATE_LIB_OK=TRUE` on every `cairn` invocation (OpenMP conflict on
this machine), ASG server on `:4751`, cairn repo on `:4747`.