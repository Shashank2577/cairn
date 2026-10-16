"""Blind accuracy on a third real product (GoogleCloudPlatform/microservices-demo, Online Boutique, at a
pinned commit) — the product that made the SC-001 number unbiased again after the robot-shop fixes were
developed against that fixture.

Ground truth committed on its own (7beab4d) before any extraction ran, written from the README service
table, the architecture diagram and the Kubernetes manifest names only. Measured BLIND, rules frozen:
elements 0.92 / 0.79 and relationships 0.00 / 0.00 — every service-to-service call is gRPC, for which
there were no rules. The gRPC rules, configured-address wiring and image-with-source attribution
(T058, T059) were then developed against this fixture, so it is now a regression floor like robot-shop:
elements 0.92 / 0.86, relationships 0.94 / 0.84. The remaining misses are the ECB rates fetch (flagged
"uncertain from docs alone" in the truth); the extra elements are real at this commit but absent from
the doc-derived truth (shoppingassistantservice and the Postgres stores the code actually uses — the
README says productcatalog reads a JSON file; the code reads Postgres). A fourth product is needed for
the next unbiased round. See specs/004-system-extraction/blind-accuracy-online-boutique.md.
"""
from __future__ import annotations

from pathlib import Path

from .fixtures.fetch import pinned_repo
from .scoring import load_truth, score, summary
from .test_views_fixtures import aliases
from .util import copy_fixture

FIXTURES = Path(__file__).parent / "fixtures"
PIN = "38e7348eb289eb5b87c0c6e8cb19ced0449dc389"
# blind 2026-10-15: 0.92/0.79 elements, 0.00/0.00 relationships; after T058/T059 (same day): below.
FLOOR = {"element_recall": 0.92, "element_precision": 0.85, "relationship_recall": 0.94,
         "relationship_precision": 0.83}


def test_online_boutique_blind_accuracy_floor(tmp_path):
    """SC-001 regression floor (FR-006, FR-013): the gRPC product whose 0.00 drove T058 (T002)."""
    from cairn.system.link import link
    from cairn.system.views import view
    src = pinned_repo("https://github.com/GoogleCloudPlatform/microservices-demo", PIN, "online-boutique")
    root = copy_fixture(src, tmp_path / "online-boutique", git=False)
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(FIXTURES / "online-boutique" / "GROUND_TRUTH.yaml"), aliases(pm))
    print("\n" + summary("online-boutique (after T058/T059)", s))
    for k, floor in FLOOR.items():
        assert s[k] >= floor, (k, s[k], s)
