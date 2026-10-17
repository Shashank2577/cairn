import os

import psycopg
import stripe
from fastapi import FastAPI

from .producer import publish

app = FastAPI()


def db():
    return psycopg.connect(os.environ['DATABASE_URL'])


@app.post('/invoices')
def create_invoice(payload: dict) -> dict:
    with db() as conn:
        conn.execute("INSERT INTO invoices (amount) VALUES (%s)", (payload['amount'],))
    publish('order.placed', payload)
    stripe.PaymentIntent.create(amount=payload['amount'], currency='usd')
    return {'status': 'created'}


@app.get('/invoices/{invoice_id}')
def read_invoice(invoice_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT id FROM invoices WHERE id = %s", (invoice_id,)).fetchone()
    return {'id': row[0]}
