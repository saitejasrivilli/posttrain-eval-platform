from kafka import KafkaConsumer, KafkaProducer

from app.config import settings


def make_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: v.encode("utf-8"),
        acks="all",
    )


def make_consumer(topic: str, group_id: str) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        value_deserializer=lambda v: v.decode("utf-8"),
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
