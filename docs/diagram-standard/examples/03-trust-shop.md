**Containers of Shop, network and trust boundaries**: Only the storefront and the API publish ports to the host; the database, Redis and the worker are reachable only on the compose network; nothing checks who calls the API.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| Customer | person |  | Reaches the storefront from the internet | system.yaml: actors (declared) |
| shop-web | web-app | Node.js · port 3000 published | Public storefront | shop-api/docker-compose.yml: web ports 3000:3000 (extracted) |
| shop-api | service | FastAPI · port 8000 published | Orders API | shop-api/docker-compose.yml: api ports 8000:8000; shop-api/shop_api/orders.py: no auth dependency on routes (inferred) |
| PostgreSQL | data-store | postgres 16 | Orders | shop-api/docker-compose.yml: db (no ports) (extracted) |
| Redis | channel | redis 7 | Order events | shop-api/docker-compose.yml: redis (no ports) (extracted) |
| shop-worker | worker | Python | Holds the SendGrid key | shop-api/docker-compose.yml: services.worker environment (a SendGrid credential) (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | Customer | shop-web | Places orders | HTTPS · host port 3000 | shop-api/docker-compose.yml: web ports (extracted) |
| 2 | shop-web | shop-api | Creates and reads orders | HTTP · api:8000 | shop-api/docker-compose.yml: API_BASE_URL (extracted) |
| 3 | shop-api | PostgreSQL | Reads and writes orders | SQL · DATABASE_URL | shop-api/docker-compose.yml: DATABASE_URL (extracted) |
| 4 | shop-api | Redis | Publishes order.created | Redis · REDIS_URL | shop-api/docker-compose.yml: REDIS_URL (extracted) |
| 5 | shop-worker | Redis | Subscribes to order.created | Redis · REDIS_URL | shop-api/docker-compose.yml: worker REDIS_URL (extracted) |
