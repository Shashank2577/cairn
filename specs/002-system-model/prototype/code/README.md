# Throwaway prototype code (not part of `src/`)

Kept so the prototype's claims can be re-run and checked. The real build replaces it (see tasks.md).

```sh
cd specs/002-system-model/prototype
# the sandbox repos need git history for `git ls-files`:
for r in sandbox/shop-*; do (cd $r && git init -q && git add -A && git -c user.name=x -c user.email=x@x commit -qm init); done
python3 code/extract.py sandbox /tmp/containers.yaml          # 0 model calls, read-only
python3 code/diagram.py render /tmp/containers.yaml            # checks against the standard, writes SVG + evidence table
python3 code/diagram.py check ../../../docs/diagram-standard/examples/*.yaml
python3 code/diagram.py check --hand-made ../../../docs/diagram-standard/taazaa-original/*.yaml   # expect 47 and 53 violations
```

`extract.py` imports `cairn.engines.graph.repos.declarations` from this checkout's `src/`. Note the ground
truth (`sandbox/GROUND_TRUTH.md`) was written by the same author as the sandbox, so the score is a smoke test,
not evidence for SC-001; real fixtures with ground truth written before extraction are task T002.
