**Data model of Shop orders**: An Order is stored as one orders row, and an OrderCreated event refers to an order by its id.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| Order | entity |  |  | shop-contracts/shop_contracts/orders.py:4 (extracted) |
| orders (table) | entity |  |  | shop-api/shop_api/db.py:8 (extracted) |
| OrderCreated | entity |  |  | shop-contracts/shop_contracts/orders.py:10 (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | Order | orders (table) | Persisted as one row |  | shop-api/shop_api/db.py:8 (extracted) |
| 2 | OrderCreated | Order | Refers to by order_id |  | shop-api/shop_api/orders.py:13 (extracted) |
