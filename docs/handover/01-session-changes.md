# Session handover 1 — what changed, from the P2 baseline to today

**Baseline:** `04ee8d6` *"ci: render every Mermaid export with the Mermaid CLI (T050)"*
**Written:** 2026-10-10 · **Scope:** the working tree as it stands, uncommitted

Read this to know what is true of the code right now. Read
[02-product-identity-design.md](02-product-identity-design.md) for the open design question.

---

## 0. How to read this document

There are two categories of change in the working tree, and conflating them is the fastest way to
misread the repo:

| Category | What it is | How to inspect |
|---|---|---|
| **Committed baseline** | Everything up to `04ee8d6`. The product as it shipped. | `git show 04ee8d6` |
| **Uncommitted delta** | Specs 002–008 in flight **plus** this session's work. | `git diff HEAD`, `git status` |

**Nothing below is committed.** If you `git checkout .` you lose all of it. There are no stashes, no
branches, no safety net. Treat the working tree as the only copy.

Test status at time of writing:

| Suite | Result |
|---|---|
| `tests/engines/` | **875 passed** |
| `tests/system/` (`CAIRN_UI_TESTS=1`, server on :4747) | **792 passed** |
| `tests/test_global_install.py` | **2 failed — pre-existing**, fail identically on stashed original code (`ModuleNotFoundError: No module named 'tests'`) |

---

## 1. Delta size

```
36 tracked files changed, 1,763 insertions(+), 272 deletions(-)
 6 new untracked source files
16 new untracked test files
```

Tracked, by area:

| File | ± | What changed |
|---|---|---|
| `src/cairn/server.py` | +217 | System diagram endpoints, **architecture caching**, product resolution for the session payload, graph view routes |
| `src/cairn/engines/graph/repos.py` | +173 | `system.yaml` product awareness, declared/shared link fallbacks |
| `src/cairn/system/views.py` | +202 | context/containers payloads now carry `system` and `repos` |
| `src/cairn/ui/app/views/map.js` | +133 | System tab, **Connections tab**, product banner, repo list grouping |
| `src/cairn/system/signals/routes.py` | +195 | route extraction rework |
| `src/cairn/core.py` | +123 | |
| `src/cairn/cli.py` | +101 | |
| `src/cairn/system/link.py` | +217 | product linking |
| `src/cairn/ui/app/components/shell.js` | +35 | **product row in the project switcher** |
| `src/cairn/system/diagram/svg.py` | +22 | **`render_inline()`** |
| `src/cairn/ui/styles/views.css` | +24 | engine frame, product group headings |
| `src/cairn/ui/app/api.js` | +4 | `systemView`, `systemFlows`, `diagramTokens` |
| `src/cairn/ui/styles/shell.css` | +14 | product row styling |

New untracked source: `src/cairn/system/{components,export,flows,narrate,recheck}.py`,
`src/cairn/ui/app/components/c4view.js`.

New untracked tests: 16 files under `tests/system/`, including `test_system_diagram.py` (13 tests,
9 of them browser tests gated on `CAIRN_UI_TESTS=1`) and three fixture trees.

---

## 2. What this session actually did

Only these. Everything else in the delta is specs 002–008 from earlier sessions.

### 2.1 `render_inline()` — UI draws the canonical SVG

`src/cairn/system/diagram/svg.py`

The System tab embeds `GET /api/p/{pid}/system/svg`, which returns `render_inline()` output: the
diagram standard's own SVG with its `<style>` stripped and `var(--x)` remapped to the UI's `--dg-*`
tokens. The UI draws **no geometry**. This is the fix for the earlier rewrite where `c4view.js`
re-implemented layout in JS (`TYPE_COLOR`, per-type grouping) and was unusable.

`src/cairn/ui/app/components/c4view.js` adds only: hover, click-to-evidence, keyboard focus, a
loading indicator (`Redrawing…`), and fit / actual-size control.

### 2.2 Architecture endpoint caching — 50.7s → 1.0s

`src/cairn/server.py`

`/graph/architecture` took **50.7 seconds** for the cairn repo (16,911 nodes). The whole page blocks
on that fetch, so the project name, the tabs, everything was invisible for 50 seconds. This was
reported as "the UI is broken overall" and was in fact one bug.

`architecture()` in `src/cairn/core.py:834` rebuilds the whole file graph and re-rolls it into
dependency layers on every call. Added `_map_stamp()` + `_architecture_cached()` — the same
`lru_cache`-on-stamp pattern already used for System diagrams — keyed on link count + HEAD commit.

| | before | after |
|---|---|---|
| cold | 50.7 s | 1.0 s |
| warm | 50.7 s | 0.009 s |

**Pre-existing**, from the original 0.2.0 commit. Not introduced by this session.

### 2.3 Reverted a dead `+N more` I had added

`src/cairn/ui/app/components/archmap.js` — restored to `HEAD`.

The earlier C4 work added a per-layer budget that sliced members of an expanded import cycle to 9 and
emitted a `+N more in this cycle group` box that **did nothing when clicked**. Reverted. Cycles now
render every member; expanding "cycle of 106 folders" goes 14 → 119 boxes with no truncation.

### 2.4 Connections tab (reverted T032)

`src/cairn/server.py`, `src/cairn/ui/app/views/map.js`, `src/cairn/ui/styles/views.css`

T032 removed the `/graph/views/{kind}` UI routes. Restored as the **Connections** tab with three
views, each labelled with what it is for:

- **Interactive graph** — vis-network, pan/zoom, node inspector, community filter
- **Architecture and call flow** — navigable document, community diagram, per-area call flows
- **Folder tree** — collapsible

Rendered on demand by the engine and served in a sandboxed same-origin iframe. Nothing is
precomputed into repositories, so they cannot go stale.

### 2.5 The product becomes visible — three surfaces, one resolution

This is the substantive change and the reason to read this section twice.

**The core defect:** `system.yaml` declares a product. The **System tab** honoured it. The
**Projects map** did not — `declarations()` in `src/cairn/engines/graph/repos.py` inferred
relationships purely from **package imports**, and your ASG services are separate Spring Boot apps
that call each other over HTTP. So the graph drew 10 flat islands and 0 edges.

Changes:

| Layer | File | Change |
|---|---|---|
| Reader | `repos.py` `_product()` | Reads `system.yaml` via the existing `systems.discover()`; returns `{system, members, description}` |
| Reader | `repos.py` `declarations()` | Now returns a `product` key alongside the ecosystem keys |
| Resolution | `repos.py` `repos()` | Builds one `root → product` map across all projects; attaches `product` to each node |
| Links | `repos.py` `_shared_element_links()` | When import links are empty, link repos sharing an external element (`kind: shares`) |
| Links | `repos.py` `_declared_product_links()` | `system.yaml` `relationships` / `actors` → `kind: declared`, wins over `shares` |
| API | `server.py` `_products_by_project()` | Attaches `product: {system, members, anchor}` to `/api/session` |
| UI | `shell.js` | Product becomes a **selectable row** in the project switcher |
| UI | `map.js` `RepoView` | Repository list grouped by product |
| UI | `map.js` `SystemView` | Banner naming the product and its repositories |

Result on the ASG product — **0 links became 3 real ones**:

```
Audit-Service    - shares -> User-Service     postgresql, kafka
Audit-Service    - shares -> Documents-Service postgresql
Documents-Service- shares -> User-Service     postgresql
```

All 5 repos, **22,707 nodes** total, grouped as `ASG Edge+ · 5 repositories`.

### 2.6 What the product is *not* yet

**The product is not an entity.** Cairn's spine is `pid`; every route is `/p/{pid}/…`, every table has
`project_id`, memory and specs are per-repository. A product has no id, no row, no route.

So after all of the above, all six Map tabs still report:

```
explore        The code and documents of ASG-Edgeplus-Audit-Service … 762 nodes
connections    The code and documents of ASG-Edgeplus-Audit-Service … 762 nodes
architecture   The code and documents of ASG-Edgeplus-Audit-Service … 762 nodes
wiki           The code and documents of ASG-Edgeplus-Audit-Service … 762 nodes
report         The code and documents of ASG-Edgeplus-Audit-Service … 762 nodes
```

762 nodes is one repository. The product is 22,707. Only **System** draws all five, and only because
`system.yaml` happens to live inside Audit-Service.

---

## 3. The one trap that bit three times

> **Only the repository that owns `system.yaml` has a `system.yaml`.** The other four members return
> `None` when asked directly.

Every product lookup must be keyed on **resolved member roots**, never on "does this repo declare a
product". Symptoms seen, in order:

1. `/api/repos/map` grouped nothing — every repo looked equally undeclared.
2. `_products_by_project_key` reported `members: 1`.
3. Same function then dropped four of five repositories entirely.

All three were the same bug. If you touch product resolution, resolve it **once across all projects**
and match member roots. Recorded in Cairn memory as `ed3164c38d`.

---

## 4. Process failures worth naming

These were mine, and they cost the most time in this session.

1. **I answered the literal request instead of the question asked.** Asked "where is the project
   itself?", I fixed the dropdown. The product remains un-selectable in any structural sense.
2. **I verified on the wrong repository.** Asserted the Architecture fix worked, having tested
   `p_54e5109…` (ASG Audit) instead of `p_db09261c…` (cairn), the one the user was pointing at.
3. **I asserted a cause I had not checked.** Said "cycle of 106 folders" was my truncation; it is
   original, intentional design with a working Expand button. The truncation was a real but
   different bug.
4. **I reported partial delivery as complete.** The summary in `02-product-identity-design.md` §5
   lists these. Read it before trusting any progress claim from an agent on this repo.

---

## 5. Environment facts you need

- **Ports.** ASG server on `4751` (`cairn serve --port 4751`), cairn repo on `4747`. `cairn ui` has
  **no `--port` flag** — it is `cairn serve --port`.
- **`KMP_DUPLICATE_LIB_OK=TRUE` is required** for every `cairn` invocation on this machine. Without
  it: `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.`
- **ASG project ids:** Audit `p_54e51090852cdb62`, User `p_99b6278baefc6ab7`, Configuration
  `p_787397f4c09b508b`, Documents `p_93da74895b29f196`, Api-Gateway `p_b101f57889df1691`.
- **The last four were registered this session.** They had `.cairn/` on disk but were unknown to the
  server, which is why Projects showed one ASG repo out of five.
- **Project registry is a side effect.** `register_local_project()` writes to the platform DB. Four
  rows were added outside the repo's own working tree.
- **`.cairn/` directories** were created in all five ASG repos. Nothing committed. Untracked there too.
- **Generated docs are checked in CI.** `scripts/sync_diagram_docs.py --check` and
  `scripts/sync_tokens.py --check` fail the build when stale.
- **Product membership resolves from registered roots.** Add a sixth repo to `system.yaml` without
  registering it and the count still says 5.

---

## 6. Open items, in priority order

| # | Item | State |
|---|---|---|
| 1 | **Product as a first-class entity** | Design decision needed — see doc 02 |
| 2 | Nine named architecture documents | Not built. Export emits 255+ per-diagram files. `configuration_table()` in `src/cairn/system/export.py` is written but never called |
| 3 | `ASG-Edgeplus-UI` | Never initialised, absent from `system.yaml`. You named user/config/audit/ui/gateway; what exists is user/config/audit/**documents**/gateway |
| 4 | Spring config-URL resolution | Gateway→service arrows absent; services wire each other via `application.yml`, not imports |
| 5 | Extract-window alignment | `ASG-Edgeplus-Configuration-Service` has 7 uncommitted files, User 11, Api-Gateway 6 — never touched |
| 6 | Per-architecture doc comparison | Your `ASG-AssetStrategiesGroup-EdgePlus/docs/` reference was never diffed against Cairn's output |

---

## 7. Verifying this document's claims

Every number here was measured, not estimated. To re-check:

```bash
# delta
git diff --stat HEAD
git status --short | grep '^??'

# suites (a server must be listening on 4747 for the UI tests)
KMP_DUPLICATE_LIB_OK=TRUE .venv/bin/cairn serve --port 4747 &
.venv/bin/pytest tests/engines/           # expect 875 passed
CAIRN_UI_TESTS=1 .venv/bin/pytest tests/system/   # expect 792 passed

# the 50s bug, fixed
curl -s -o /dev/null -w "%{time_total}s\n" \
  localhost:4751/api/p/p_db09261cb4710177/graph/architecture

# product resolution — all five must agree
curl -s localhost:4751/api/session | python3 -c \
 'import sys,json;[print(p["name"],p["product"]) for p in json.load(sys.stdin)["projects"] if p.get("product")]'

# product links
curl -s localhost:4751/api/repos/map | python3 -c \
 'import sys,json;d=json.load(sys.stdin);print(len(d["nodes"]),"nodes",len(d["links"]),"links")'
```