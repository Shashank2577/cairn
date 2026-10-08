import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_confirmation(event) -> None:
    client = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    client.send(Mail(from_email="orders@shop.example", to_emails=event.customer_email,
                     subject="Order received", plain_text_content=f"Order {event.order_id} received."))
