"""A repository with known problems seeded into a branch, for the review tests.

Seeded on branch `feature` (base `main`):
  cochange   shop/ledger.py usually changes with shop/payments.py but is missing from the change
  test       tests/test_payments.py usually changes with shop/payments.py but is missing
  history    shop/payments.py has a past fix ("Fix double charge on retry")
  convention a stored convention about shop/payments.py
  secret     a credential-shaped value is added to shop/payments.py
  lock       package.json dependencies change while package-lock.json does not
"""
from __future__ import annotations

import json

from tests.conftest import ENV, commit, git  # noqa: F401  (ENV re-exported for callers)

PAY = 'def charge(order, cents):\n    return {"order": order, "cents": cents}\n'
LEDGER = "def record(order, cents):\n    return (order, cents)\n"
TEST = "from shop.payments import charge\n\n\ndef test_charge():\n    assert charge('o', 1)['cents'] == 1\n"
PKG = json.dumps({"name": "shop", "dependencies": {"left-pad": "^1.0.0"}}, indent=2) + "\n"
LOCK = json.dumps({"name": "shop", "lockfileVersion": 3}, indent=2) + "\n"

SECRET_LINE = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'

SEEDED = {"cochange": "shop/ledger.py", "test": "tests/test_payments.py", "history": "Fix double charge on retry",
          "convention": "integer cents", "secret": "AWS access key", "lock": "package-lock.json"}


def build(root) -> None:
    git(root, "init", "-q", "-b", "main")
    commit(root, {"shop/__init__.py": "", "shop/payments.py": PAY, "shop/ledger.py": LEDGER,
                  "tests/test_payments.py": TEST, "package.json": PKG, "package-lock.json": LOCK}, "Add shop")
    for i in range(4):
        commit(root, {"shop/payments.py": PAY + f"# rev {i}\n", "shop/ledger.py": LEDGER + f"# rev {i}\n",
                      "tests/test_payments.py": TEST + f"# rev {i}\n"}, f"Update payments {i}")
    commit(root, {"shop/payments.py": PAY + "# retry guard\n"}, "Fix double charge on retry")
    git(root, "checkout", "-q", "-b", "feature")
    commit(root, {"shop/payments.py": PAY + "# retry guard\n" + SECRET_LINE + "def refund(order):\n    return order\n"},
           "Add refund")
    commit(root, {"package.json": json.dumps({"name": "shop", "dependencies": {"left-pad": "^1.0.0",
                                                                               "lodash": "^4.17.21"}}, indent=2) + "\n"},
           "Add lodash")
