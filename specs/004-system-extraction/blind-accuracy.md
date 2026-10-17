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
| **nginx proxy config not read**: `web` is nginx serving a UI and proxying `/api/*` | element `web`; 6 relationships `web -> catalogue/user/cart/shipping/payment/ratings` | T052: a `config` catalog rule for nginx `location ... proxy_pass http://<service>:<port>` |
| **PHP not in the catalog**: `ratings` | element `ratings`; `ratings -> mysql`, `ratings -> catalogue` | T053: PHP framework and client rules (the grammar is already bundled) |
| **Service URLs built from env names at runtime**, e.g. a host variable plus a path | `cart -> catalogue`, `shipping -> cart`, `payment -> cart`, `payment -> user` (payment's calls landed on "Unresolved HTTP target", inferred) | T054: resolve host variables whose default or compose value names a compose service |
| **Store built from a directory, not an image**: compose `mysql` uses `build: mysql` | element `mysql` (extracted as a generic "SQL database", a false element); `shipping -> mysql` counted false | T055: name stores from the compose service when its build context is a database image |
| **External gateway in config only** | `payment-gateway`, `payment -> payment-gateway` | T056: config-URL rule for external hosts (marked inferred) |
| **Budget collapse counted as an element** | "+6 more" scored as a false element (13 elements exceed the 12 budget) | T057: scoring should expand collapsed groups; the view should not collapse a 13-element product |

## Does the shell-HTTP rule generalise?

Inconclusive. robot-shop has one shell HTTP call (`web/entrypoint.sh`, `curl` to a URL in an environment
variable) inside a container that was not detected, and the rule produced no false relationship from it.
Its only positive evidence is still the voting app plus unit tests.

## Re-run after the T052 to T057 fixes (2026-10-13)

The fixes above were implemented as general rules — an nginx `proxies` catalog section and parser
(`signals/proxy.py`), PHP language support with curl and PDO rules (T053), configuration fallbacks recorded
beside every environment read and resolved to deploy units (T054, in Python `.format` URLs, JavaScript `||`
and the `request` client, Java `getenv(k, def)` helpers and ternaries), stores from build-context
Dockerfiles whose image and command say store (T055), outside systems named after their configuration key
when a configured DOMAIN address leaves the product (T056), and soft view budgets with a group that carries
its members, which the scorer expands (T057).

| | Recall | Precision (extracted claims) |
|---|---|---|
| Elements | **1.00** (13 of 13) | **0.86** (11 of 13) |
| Relationships | **1.00** (20 of 20) | **1.00** |
| Labelled inferred or ambiguous | 6% of the view | |

Every miss from the blind run is closed. The two false elements are `fluentd` (a Dockerfile in the
repository no compose file references) and `load` (the load generator in `docker-compose-load.yaml`, which
the truth scopes out): both are real deployable pieces of the repository, extracted honestly, and left in.

**This is no longer a blind number.** The rules were developed while reading this fixture, so robot-shop
now measures regression, not unbiased accuracy (the test pins these values as a floor). An honest SC-001
claim still needs a third real product nobody working on the feature has looked at (T002). The shop,
orders and voting-app fixtures all still score 1.00/1.00.
