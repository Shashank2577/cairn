**Containers of Shop, personal data**: The customer's email address travels from the storefront to the API, into PostgreSQL and Redis, through the worker and out to SendGrid.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| shop-web | web-app | Node.js | Collects the order form | shop-web/server.js:5 (extracted) |
| shop-api | service | FastAPI | Validates and stores orders | shop-api/shop_api/orders.py (extracted) |
| PostgreSQL | data-store | orders.customer_email | Keeps the email with each order | shop-api/shop_api/db.py:8 (extracted) |
| Redis | channel | order.created | Event carries the email | shop-contracts/shop_contracts/orders.py:12 (extracted) |
| shop-worker | worker | Python | Builds the confirmation | shop-worker/shop_worker/mailer.py (extracted) |
| SendGrid | external-system |  | Processes email outside Shop | shop-worker/pyproject.toml: sendgrid (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | shop-web | shop-api | Sends the order | POST /api/orders | shop-web/src/api.js:3 (extracted) |
| 2 | shop-api | PostgreSQL | Stores the order | INSERT orders | shop-api/shop_api/db.py:8 (extracted) |
| 3 | shop-api | Redis | Publishes OrderCreated | order.created | shop-api/shop_api/orders.py:13 (extracted) |
| 4 | shop-worker | Redis | Receives OrderCreated | order.created | shop-worker/shop_worker/main.py:10 (extracted) |
| 5 | shop-worker | SendGrid | Sends email to the customer | SendGrid SDK | shop-worker/shop_worker/mailer.py:7 (extracted) |
