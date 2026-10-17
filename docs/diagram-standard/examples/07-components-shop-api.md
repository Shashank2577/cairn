**Components of shop-api**: The FastAPI app mounts the orders router, which writes through the database module and announces new orders through the events module, using types from shop-contracts.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| shop-web | web-app | Express | Calls the orders routes | shop-web/src/api.js (extracted) |
| App entry | component | shop_api/main.py | Builds the FastAPI app and starts uvicorn | shop-api/shop_api/main.py:6 (extracted) |
| Orders routes | component | shop_api/orders.py | POST and GET /api/orders | shop-api/shop_api/orders.py:6 (extracted) |
| Order storage | component | shop_api/db.py | SQL for the orders table | shop-api/shop_api/db.py (extracted) |
| Event publisher | component | shop_api/events.py | Publishes events as JSON | shop-api/shop_api/events.py (extracted) |
| shop-contracts | library | Python package 1.2.0 | Order and OrderCreated types | shop-api/pyproject.toml: shop-contracts (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | shop-web | App entry | Sends order requests | HTTP :8000 | shop-web/src/api.js:3 (extracted) |
| 2 | App entry | Orders routes | Mounts the router | include_router() | shop-api/shop_api/main.py:7 (extracted) |
| 3 | Orders routes | Order storage | Saves and loads orders | insert_order() | shop-api/shop_api/orders.py:3 (extracted) |
| 4 | Orders routes | Event publisher | Announces new orders | publish() | shop-api/shop_api/orders.py:13 (extracted) |
| 5 | Orders routes | shop-contracts | Uses Order types | import shop_contracts | shop-api/shop_api/orders.py:2 (extracted) |
