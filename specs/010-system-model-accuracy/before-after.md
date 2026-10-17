# Before / after on instana/robot-shop (55292e21), `cairn system --view containers`

Reproduced with PR #1 head 1f1c7fd (before) and this branch (after) on the same pinned commit. After a
code change, run `cairn sync` once: `cairn system --view` reads the stored model.

| Item | Before | After |
|---|---|---|
| cart → catalogue | `[gRPC · ANY via CATALOGUE_HOST]` — `cart/server.js:30` (the `process.env` line) | `[HTTP · ANY via CATALOGUE_HOST]` — `cart/server.js:362 (lines 360-374)` (`getProduct`, where `request('http://' + catalogueHost …)` is) |
| shipping → cart | `[gRPC · ANY via CART_ENDPOINT]` — `Controller.java:24` | `[HTTP · ANY via CART_ENDPOINT]` — `Controller.java:136` (`new CartHelper(CART_URL)`) |
| load → web | `[gRPC · ANY /]` | `[ANY /]`: no protocol claimed. Locust's `self.client.get('/api/…')` is not extracted as a call, so only the configured address is known |
| payment → user | `[HTTP · GET, POST, ANY /]` | `[HTTP · GET /check/{}, POST /order/{}]`; `POST /order/{}` is `payment.py:106`, the `ANY /` config default is gone from the label |
| web | `job · Shell` | `web-app · nginx` |
| load | `job · Shell` | `job · Python` |
| fluentd | `container · Shell` | `container · Fluentd` |
| citations | `file:line` | `file:line (lines a-b)` of the enclosing function in the text view (JSON view unchanged) |

Root cause of the gRPC labels: a relationship made only of configured addresses has no client call sites, and
`0 gRPC calls == 0 calls` was read as "all calls are gRPC".

## Other output (spec 011)

| Command | Before | After |
|---|---|---|
| `cairn search password` / `email` / `8080` | nothing / nothing / nothing | text hits, e.g. `request('http://' + catalogueHost + ':8080/product/' …)  cart/server.js:362 (lines 360-374)`; `password` values are never printed |
| `cairn search cookie` | nothing | nothing (robot-shop has no cookie code) |
| `cairn hubs` | #7 `Psr\Log\LoggerAwareInterface`, #8 `Psr\Log\LoggerAwareTrait`, #11 `org.springframework…GetMapping` | none of them; the next internal files move up |
| `cairn areas` | areas named and led by `Psr\Log\LoggerAwareInterface` and `org.springframework…Bean` | area named after its top internal symbol; no library symbol among its members |

Floors: `tests/system/test_blind_robot_shop.py` and `test_blind_online_boutique.py` pass unchanged; the full suite passes.
Not fixed: manifest dependency nodes (`express`, `prom-client`, `body-parser`) still appear among area members.
