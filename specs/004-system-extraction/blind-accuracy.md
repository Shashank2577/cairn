# Blind accuracy: instana/robot-shop

Second real product, chosen so nobody working on the feature had looked at its code. Pinned commit
`55292e2` (Apache-2.0), fetched into the test cache, not vendored. Ground truth committed on its own
(`0fb6895`) before extraction ran; extraction rules were frozen while it was scored. Test:
`tests/system/test_blind_robot_shop.py` (pins these numbers as a regression floor).

| | Recall | Precision (extracted claims) |
|---|---|---|
| Elements | **0.69** (9 of 13) | **0.82** |
| Relationships | **0.30** (6 of 20) | **0.86** |
| Labelled inferred or ambiguous | 10% of the view | |

**Below SC-001** (recall ≥ 0.90, precision ≥ 0.95). The voting app's 1.00 was not representative.

## Misses, by cause

| Cause | Misses | Fix (slice 004) |
|---|---|---|
| **nginx proxy config not read**: `web` is nginx serving a UI and proxying `/api/*` | element `web`; 6 relationships `web -> catalogue/user/cart/shipping/payment/ratings` | a `config` catalog rule for nginx `location ... proxy_pass http://<service>:<port>` |
| **PHP not in the catalog**: `ratings` | element `ratings`; `ratings -> mysql`, `ratings -> catalogue` | PHP framework and client rules (the grammar is already bundled) |
| **Service URLs built from env names at runtime**, e.g. a host variable plus a path | `cart -> catalogue`, `shipping -> cart`, `payment -> cart`, `payment -> user` (payment's calls landed on "Unresolved HTTP target", inferred) | resolve host variables whose default or compose value names a compose service |
| **Store built from a directory, not an image**: compose `mysql` uses `build: mysql` | element `mysql` (extracted as a generic "SQL database", a false element); `shipping -> mysql` counted false | name stores from the compose service when its build context is a database image |
| **External gateway in config only** | `payment-gateway`, `payment -> payment-gateway` | config-URL rule for external hosts (marked inferred) |
| **Budget collapse counted as an element** | "+6 more" scored as a false element (13 elements exceed the 12 budget) | scoring should expand collapsed groups; the view should not collapse a 13-element product |

## Does the shell-HTTP rule generalise?

Inconclusive. robot-shop has one shell HTTP call (`web/entrypoint.sh`, `curl` to a URL in an environment
variable) inside a container that was not detected, and the rule produced no false relationship from it.
Its only positive evidence is still the voting app plus unit tests.
