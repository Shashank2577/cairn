**System context for Shop**: Customers place orders through Shop, which emails each customer a confirmation through SendGrid.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| Customer | person |  | Browses the store and places orders | system.yaml: actors (declared) |
| Shop | system |  | Takes orders and confirms them by email | system.yaml: shop-web, shop-api, shop-worker, shop-contracts (extracted) |
| SendGrid | external-system |  | Delivers transactional email | shop-worker/pyproject.toml: sendgrid; shop-worker/shop_worker/mailer.py:2 (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | Customer | Shop | Places and tracks orders |  | shop-web/server.js:5 (declared) |
| 2 | Shop | SendGrid | Sends order confirmations |  | shop-worker/shop_worker/mailer.py:6 (extracted) |
