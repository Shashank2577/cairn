"""Write the computed Containers views of the offline test fixtures as Mermaid files (light and dark).

Used by .github/workflows/diagrams.yml so real computed output, not only hand-written examples, is rendered
by the Mermaid CLI. No network, no model calls. Usage: python scripts/export_fixture_views.py OUT_DIR
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn.system.diagram.mermaid_out import to_mermaid  # noqa: E402
from cairn.system.views import view  # noqa: E402
from tests.system.util import product  # noqa: E402

FIXTURES = {"shop": ("shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"]),
            "orders": ("checkout-spring", ["checkout-spring", "inventory-go"])}


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for name, (viewer, repos) in FIXTURES.items():
        with tempfile.TemporaryDirectory() as tmp:
            root = product(Path(tmp), name, viewer, repos)
            d = view(root, "container")
            for theme in ("light", "dark"):
                (out / f"fixture-{name}-{theme}.mmd").write_text(to_mermaid(d, theme=theme), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "out")))
