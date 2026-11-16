# rasulov/common.py
import os

import pika

RABBIT_HOST = os.getenv('RABBIT_HOST', '127.0.0.1')
RABBIT_PORT = int(os.getenv('RABBIT_PORT', '5672'))
RABBIT_USER = os.getenv('RABBIT_USER', 'guest')
RABBIT_PASSWORD = os.environ['RABBIT_PASSWORD']

PREFIX = 'ikbo-65-23_rasulov'
QUEUE = PREFIX
EXCHANGE = PREFIX + '_exchange'
EXCHANGE_TYPE = 'fanout'
QUEUE_DURABLE = False
QUEUE_AUTO_DELETE = True
BINDING_KEY = ''

def connect() -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASSWORD)
    parameters = pika.ConnectionParameters(RABBIT_HOST, RABBIT_PORT, credentials=credentials)
    return pika.BlockingConnection(parameters)

def setup(channel) -> None:
    """Объявляет обменник, очередь и привязку; идемпотентна, вызывается и поставщиком, и потребителем."""
    channel.exchange_declare(exchange=EXCHANGE, exchange_type=EXCHANGE_TYPE, durable=QUEUE_DURABLE)
    channel.queue_declare(queue=QUEUE, durable=QUEUE_DURABLE, auto_delete=QUEUE_AUTO_DELETE)
    channel.queue_bind(exchange=EXCHANGE, queue=QUEUE, routing_key=BINDING_KEY)
