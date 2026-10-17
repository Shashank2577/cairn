# Intended architecture (ground truth for scoring the prototype)
Containers: shop-web (Node/Express web app), shop-api (Python/FastAPI service), shop-worker (Python worker),
shop-contracts (library, not a running container), PostgreSQL (database), Redis (message bus / pub-sub).
External: SendGrid (email). Person: Customer (uses shop-web).
Relationships:
- Customer -> shop-web: places orders [HTTPS]
- shop-web -> shop-api: creates and reads orders [HTTP, POST /api/orders, GET /api/orders/{id}]
- shop-api -> PostgreSQL: reads and writes orders [SQL / psycopg]
- shop-api -> Redis: publishes order.created [Redis pub/sub]
- shop-worker -> Redis: subscribes to order.created [Redis pub/sub]
- shop-worker -> SendGrid: sends confirmation email [HTTPS API]
- shop-api, shop-worker -> shop-contracts: use shared types [package dependency, build time]
