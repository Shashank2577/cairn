import os

from kafka import KafkaProducer


def publish(topic: str, payload: dict) -> None:
    KafkaProducer(bootstrap_servers=os.environ['KAFKA_URL']).send(topic, payload)
