from fastapi import APIRouter
from shop_contracts import Order, OrderCreated
from .db import insert_order, get_order
from .events import publish

router = APIRouter()

@router.post("/api/orders")
def create_order(order: dict) -> dict:
    o = Order(**order)
    insert_order(o)
    publish("order.created", OrderCreated(order_id=o.id, customer_email=o.customer_email))
    return {"id": o.id}

@router.get("/api/orders/{order_id}")
def read_order(order_id: str) -> dict:
    return get_order(order_id)
