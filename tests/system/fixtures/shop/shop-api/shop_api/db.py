import os
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]

def insert_order(o):
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("INSERT INTO orders (id, customer_email, total_cents) VALUES (%s, %s, %s)",
                     (o.id, o.customer_email, o.total_cents))

def get_order(order_id):
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute("SELECT id, customer_email, total_cents FROM orders WHERE id = %s", (order_id,)).fetchone()
    return {"id": row[0], "customer_email": row[1], "total_cents": row[2]}
# ruff: noqa
