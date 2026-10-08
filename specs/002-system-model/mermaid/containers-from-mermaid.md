**Containers of Shop**: The storefront calls the orders API; the API stores orders in PostgreSQL and publishes order.created on Redis, which the worker consumes to email the customer through SendGrid.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| Customer | person |  | Places orders | system.yaml: actors (declared) |
| shop-web | web-app | Node.js · Express | Storefront pages and checkout | shop-web/package.json; shop-web/Dockerfile: EXPOSE 3000 (extracted) |
| shop-api | service | Python · FastAPI | Creates and serves orders | shop-api/pyproject.toml: [project.scripts] (extracted) |
| PostgreSQL | data-store | postgres 16 | Stores orders | shop-api/docker-compose.yml: db image postgres:16 (extracted) |
| Redis | channel | redis 7 pub/sub | Carries order events | shop-api/docker-compose.yml: redis image redis:7 (extracted) |
| shop-worker | worker | Python | Emails order confirmations | shop-worker/pyproject.toml: [project.scripts] (extracted) |
| shop-contracts | library | Python package | Shared order types | shop-contracts/pyproject.toml (extracted) |
| SendGrid | external-system |  | Delivers email | shop-worker/pyproject.toml: sendgrid (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | Customer | shop-web | Places orders | HTTPS · POST /checkout | shop-web/server.js:5 (declared) |
| 2 | shop-web | shop-api | Creates and reads orders | HTTP · /api/orders | shop-web/src/api.js:3; shop-api/shop_api/orders.py:9 (extracted) |
| 3 | shop-api | PostgreSQL | Reads and writes orders | SQL · psycopg | shop-api/shop_api/db.py:7 (extracted) |
| 4 | shop-api | Redis | Publishes order.created | Redis PUBLISH | shop-api/shop_api/events.py:8 (extracted) |
| 5 | shop-worker | Redis | Subscribes to order.created | Redis SUBSCRIBE | shop-worker/shop_worker/main.py:10 (extracted) |
| 6 | shop-worker | SendGrid | Sends confirmation email | HTTPS · SendGrid SDK | shop-worker/shop_worker/mailer.py:6 (extracted) |
| 7 | shop-api | shop-contracts | Uses shared types | package shop-contracts | shop-api/shop_api/orders.py:2 (extracted) |
| 8 | shop-worker | shop-contracts | Uses shared types | package shop-contracts | shop-worker/shop_worker/main.py:3 (extracted) |
