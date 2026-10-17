"""Setup-time budget (SC-008; T045, T045a). The baseline was measured before the system step existed and is
recorded in specs/002-system-model/plan.md: the map step took 30.8 s on this repository, so the system
step's budget is 15% of it, 4.6 s (scale with CAIRN_PERF_SCALE on slower machines)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cairn.system.build import build_repo

from .util import write

ROOT = Path(__file__).resolve().parents[2]
MAP_BASELINE_SECONDS = 30.8
# Windows runners are measurably slower on file-heavy work (4.6-5.0 s against 4.6 s on Linux/macOS), so they get 1.5x.
BUDGET = 0.15 * MAP_BASELINE_SECONDS * float(os.environ.get("CAIRN_PERF_SCALE", "1.5" if os.name == "nt" else "1"))


def _best(fn, runs: int = 2) -> float:
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_build_on_this_repository_stays_within_budget():
    """SC-008, FR-005: T045: build_repo on Cairn's own repository within 15% of the recorded map baseline."""
    seconds = _best(lambda: build_repo(ROOT, "cairn"))
    print(f"\nbuild_repo(cairn) {seconds:.2f}s, budget {BUDGET:.2f}s")
    assert seconds <= BUDGET


def _synthetic(root: Path, n: int) -> Path:
    files = {"Dockerfile": "FROM python\n", "pyproject.toml": '[project]\nname = "s"\ndependencies = ["fastapi"]\n'}
    for i in range(n):
        files[f"pkg/m{i}.py"] = (
            "import os, requests\nfrom fastapi import APIRouter\nrouter = APIRouter()\n"
            f"BASE = os.environ['BASE_{i}']\n"
            f"@router.get('/items{i}/{{x}}')\ndef h{i}(x):\n    return requests.get(f'{{BASE}}/other{i}/{{x}}')\n"
            + "\n".join(f"def f{i}_{j}(a, b):\n    return [a + b for _ in range({j})]\n" for j in range(20)))
        files[f"pkg/plain{i}.py"] = "\n".join(f"def g{j}(x):\n    return x * {j}\n" for j in range(30))
    return write(root, files)


@pytest.mark.parametrize("base", [60])
def test_build_time_scales_near_linearly(tmp_path, base):
    """SC-008: Scaling: 1x, 2x and 4x the files take at most about 2x and 4x the time (with slack for noise)."""
    times = {}
    for k in (1, 2, 4):
        root = _synthetic(tmp_path / f"x{k}", base * k)
        m = build_repo(root, "s")
        assert sum(1 for e in m.elements.values() if e.kind == "route") == base * k
        times[k] = _best(lambda: build_repo(root, "s"))
    print("\nscaling", {k: round(v, 3) for k, v in times.items()})
    assert times[2] <= 2 * times[1] * 1.6 + 0.05
    assert times[4] <= 4 * times[1] * 1.6 + 0.05


def test_layout_time_stays_within_budget(tmp_path):
    """SC-008: diagram layout on a computed view stays under 2 s (interactive use)."""
    from cairn.system.diagram.layout import layout
    from cairn.system.views import view
    from .util import product
    root = product(tmp_path, "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    desc = view(root, "containers")
    seconds = _best(lambda: layout(desc), runs=3)
    print(f"\nlayout(containers) {seconds:.3f}s")
    assert seconds <= 2.0


def test_memory_stays_within_budget(tmp_path):
    """SC-008: building the model on a 1k-file synthetic repo stays under 200 MB."""
    import os
    resource = pytest.importorskip("resource")  # POSIX only: Windows has no `resource` module
    root = _synthetic(tmp_path / "big", 1000)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    build_repo(root, "s")
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    used_mb = (after - before) / (1024 * 1024)
    print(f"\nmemory delta {used_mb:.1f} MB")
    assert used_mb <= 200


def test_5k_files_20_repos_stays_within_budget(tmp_path):
    """SC-008: a 5k-file product across 20 repositories builds within the budget."""
    repos = []
    for i in range(20):
        root = _synthetic(tmp_path / f"repo-{i:02d}", 250)  # 250 files each
        repos.append(root)
    seconds = _best(lambda: [build_repo(r, r.name) for r in repos], runs=1)
    print(f"\n20 repos x 250 files: {seconds:.2f}s")
    assert seconds <= BUDGET * 20  # 20x the single-repo budget
