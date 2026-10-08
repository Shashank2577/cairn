# Checkpoint, 2026-10-08

Branch `002-system-model`. Nothing merged to `main`.

## Tests

| Run | Result |
|---|---|
| `uv run --all-extras pytest tests/system` | 539 passed, 0 failed |
| Full suite (`pytest tests`) | 1,700 passed, 2 skipped, 0 failed, 12 min 19 s (baseline before this feature: about 6 min) |
| Ruff on new code | clean (8 findings in pre-existing lines of `cli.py`, `core.py`, `mapper.py`, `sync.py` were already there) |
| Python 3.11 parse of every changed file | ok |
| CI (Windows, macOS, Linux) | **not run yet** |

The test run doubled in length. Most of the extra time is in the performance and scaling tests and the
layers test on a synthetic 5,000-file repository; they should move behind a marker before CI.

## Tasks

28 done, 9 partial, 20 open (57). Each partial says what is missing in [tasks.md](tasks.md). The
traceability table ([traceability.md](traceability.md)) still lists requirements with no test because
their slices are open (staleness, Map views, agent context, docs export).

## The precision and recall claim, verified

Phase B reported 1.00 recall and 1.00 precision for elements and relationships on all three fixtures.
I reproduced it independently for the voting app.

- **Ground truth unchanged.** `tests/system/fixtures/voting-app/GROUND_TRUTH.yaml` has one commit
  (642954f), made before any extraction code existed. It has not been edited since.
- **How it is scored** ([scoring.py](../../tests/system/scoring.py)):
  - An element matches on equal type plus normalised name or id.
  - A relationship matches on its two endpoints in the truth's direction. Labels and line styles are not
    compared.
  - Precision counts extracted claims only.
- **`seed → vote`**, which the ground truth marked "expected to be hard":
  - Found: `seed → vote [HTTP · POST /]`.
  - Evidence: `seed-data/generate-votes.sh:4-6` (`ab -p posta … http://vote/`) and `vote/app.py:24`.
  - It comes from a generic shell-HTTP-client rule (`curl`, `wget`, `ab`, `httpie` in
    `catalog/clients.yaml`) plus a route match. Nothing in the code names the fixture.
- **Not loosened, but no longer blind.** The scoring and the ground truth were not changed after the
  extractor ran. However, the agent wrote the shell-client rule while it could see this fixture, so the
  voting app is no longer a blind test and 1.00 overstates what to expect on unseen code. The `orders`
  fixture's ground truth was written in the same uncommitted batch as the extractor, so "written before"
  cannot be proven for it. An unbiased SC-001 number needs a second real product nobody has looked at
  (task T002, slice 004).

## Quality: what I would not ship yet

1. **Not run on CI.** Windows, macOS and Linux on Python 3.11 to 3.13 are all untested.
2. **The SC-001 numbers are optimistic** for the reasons above.
3. **Dependency layers (T031a).**
   - The collapsed view is a real improvement: vendored folders are hidden, the cycle is one block, and
     edges are sparse and labelled.
   - The expanded cycle shows about 70 boxes with no "+N" budget.
   - A cycle block is labelled both "ENTRY" and "cycle".
   - The view is not yet inside Code.
4. **Generic descriptions.** Stores and channels get "Stores data" and "Carries messages", the padding
   the spec rules out.
5. **Duplicated scope wording.** The computed view's scope line says "0 model calls" twice.
6. **Not yet integrated.**
   - The existing cross-repo call and type passes (T021).
   - `cairn system --svg` (T022).
   - Mermaid exports rendered in CI (T050).
   - The tokens and geometry endpoint (T024).
7. **Not reviewed yet.** No independent verifier has reviewed phases A and B. The authors' own tests pass,
   but the plan requires a fresh-context verifier per phase and a mutation run. Neither has happened yet,
   and the Fable review is reserved for the end.
8. **Not started.** Staleness, the Map views, agent context, docs export and narrative.
