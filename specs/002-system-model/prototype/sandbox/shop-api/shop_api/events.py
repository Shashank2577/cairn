import json, os
from dataclasses import asdict
import redis

r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))

def publish(topic: str, event) -> None:
    r.publish(topic, json.dumps(asdict(event)))
