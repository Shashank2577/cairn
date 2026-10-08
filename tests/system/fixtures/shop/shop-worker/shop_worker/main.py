import json, os
import redis
from shop_contracts import OrderCreated
from .mailer import send_confirmation

def run():
    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    sub = r.pubsub()
    sub.subscribe("order.created")
    for msg in sub.listen():
        if msg["type"] == "message":
            send_confirmation(OrderCreated(**json.loads(msg["data"])))
# ruff: noqa
