**Flow of placing an order in Shop**: Checkout posts the order to the API, which stores it and publishes order.created; the worker picks the event up and sends the confirmation email.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| shop-web | web-app | Express | Storefront | shop-web/server.js (extracted) |
| shop-api | service | FastAPI | Orders API | shop-api/shop_api/orders.py (extracted) |
| PostgreSQL | data-store | postgres | Orders | shop-api/shop_api/db.py (extracted) |
| Redis | channel | pub/sub | Events | shop-api/shop_api/events.py (extracted) |
| shop-worker | worker | Python | Mailer | shop-worker/shop_worker/main.py (extracted) |
| SendGrid | external-system |  | Email | shop-worker/shop_worker/mailer.py (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | shop-web | shop-api | Create order | POST /api/orders | shop-web/src/api.js:3 (extracted) |
| 2 | shop-api | PostgreSQL | Insert order row | INSERT INTO orders | shop-api/shop_api/db.py:8 (extracted) |
| 3 | shop-api | Redis | Publish OrderCreated | PUBLISH order.created | shop-api/shop_api/events.py:8 (extracted) |
| 4 | shop-api | shop-web | Order id | 200 {id} | shop-api/shop_api/orders.py:14 (extracted) |
| 5 | Redis | shop-worker | Deliver OrderCreated | SUBSCRIBE order.created | shop-worker/shop_worker/main.py:10 (extracted) |
| 6 | shop-worker | SendGrid | Send confirmation | SendGrid SDK send() | shop-worker/shop_worker/mailer.py:7 (extracted) |
