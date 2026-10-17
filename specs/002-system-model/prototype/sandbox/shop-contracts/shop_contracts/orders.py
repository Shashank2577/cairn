from dataclasses import dataclass

@dataclass
class Order:
    id: str
    customer_email: str
    total_cents: int

@dataclass
class OrderCreated:
    """Event published on the order.created topic."""
    order_id: str
    customer_email: str
