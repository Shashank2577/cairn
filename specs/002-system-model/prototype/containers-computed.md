**Containers of Shop**: Computed by Cairn from code, manifests and deploy files with no model calls: 8 elements and 8 relationships, each with evidence.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| shop-web | service | Node.js · Express · port 3000 | Serves GET /orders/{}, POST /checkout | shop-web/Dockerfile:5; shop-web/Dockerfile:6; shop-web/package.json: scripts.start; shop-web/server.js:5; shop-web/server.js:6 (extracted) |
| shop-api | service | Python · FastAPI · port 8000 | Serves GET /api/orders/{}, POST /api/orders | shop-api/Dockerfile:5; shop-api/Dockerfile:6; shop-api/pyproject.toml:6; shop-api/shop_api/orders.py:8; shop-api/shop_api/orders.py:15 (extracted) |
| shop-worker | worker | Python | Consumes order.created | shop-worker/Dockerfile:5; shop-worker/pyproject.toml:6 (extracted) |
| shop-contracts | library | Python package | Shared code used at build time | shop-contracts: provides shop-contracts; no entry point or Dockerfile (extracted) |
| PostgreSQL | data-store | PostgreSQL 16 | Stores data | shop-api/docker-compose.yml: services.db image postgres:16; shop-api/shop_api/db.py:2 (extracted) |
| Redis | channel | Redis 7 | Carries messages | shop-api/docker-compose.yml: services.redis image redis:7; shop-api/shop_api/events.py:3; shop-worker/shop_worker/main.py:2 (extracted) |
| SendGrid | external-system |  | Delivers transactional email | shop-worker/shop_worker/mailer.py:2 (extracted) |
| Customer | person |  | Places orders | system.yaml: actors (declared) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | shop-api | PostgreSQL | Writes and reads orders | SQL · psycopg | shop-api/shop_api/db.py:2; shop-api/shop_api/db.py:8 (extracted) |
| 2 | shop-api | Redis | Publishes order.created | Redis PUBLISH | shop-api/shop_api/orders.py:12 (extracted) |
| 3 | shop-api | shop-contracts | Uses shared types | package shop-contracts | shop-api/shop_api/orders.py:2 (extracted) |
| 4 | shop-worker | SendGrid | Sends email | HTTPS · SendGrid SDK | shop-worker/shop_worker/mailer.py:2 (extracted) |
| 5 | shop-worker | Redis | Subscribes to order.created | Redis SUBSCRIBE | shop-worker/shop_worker/main.py:9 (extracted) |
| 6 | shop-worker | shop-contracts | Uses shared types | package shop-contracts | shop-worker/shop_worker/main.py:3 (extracted) |
| 7 | shop-web | shop-api | Calls the orders API | HTTP · GET, POST /api/orders | shop-web/src/api.js:3; shop-web/src/api.js:7; shop-api/shop_api/orders.py:8; shop-api/shop_api/orders.py:15 (extracted) |
| 8 | Customer | shop-web | Places orders | HTTPS | system.yaml: actors (declared) |
