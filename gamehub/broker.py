import json
import logging
import threading

import pika
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

EXCHANGE = "gamehub"
SUBSCRIPTIONS = {
    "catalog": ("publisher.blocked",),
    "license": ("order.created", "order.canceled", "order.confirmed"),
    "order": ("payment.confirmed", "payment.failed", "license.reserve_failed", "library.issued"),
    "payment": ("order.created", "order.canceled"),
    "library": ("order.confirmed",),
}

tracer = trace.get_tracer(__name__)


class Broker:
    def __init__(self, url, service):
        self.url = url
        self.service = service
        self.stop = threading.Event()
        self.ready = threading.Event()

    def connect(self):
        params = pika.URLParameters(self.url)
        params.socket_timeout = 5
        params.blocked_connection_timeout = 5
        return pika.BlockingConnection(params)

    def send(self, event, data, parent_headers):
        context = propagate.extract(parent_headers)
        with tracer.start_as_current_span(event, context=context, kind=SpanKind.PRODUCER):
            headers = {}
            propagate.inject(headers)
            with self.connect() as connection:
                channel = connection.channel()
                channel.exchange_declare(EXCHANGE, "topic", durable=True)
                channel.confirm_delivery()
                channel.basic_publish(
                    EXCHANGE, event, json.dumps(data),
                    pika.BasicProperties(
                        headers=headers, content_type="application/json", delivery_mode=2
                    ),
                )

    def start(self, handle):
        keys = SUBSCRIPTIONS.get(self.service)
        if not keys:
            self.ready.set()
            return
        threading.Thread(target=self.consume, args=(keys, handle), daemon=True).start()

    def consume(self, keys, handle):
        while not self.stop.is_set():
            try:
                with self.connect() as connection:
                    channel = connection.channel()
                    channel.exchange_declare(EXCHANGE, "topic", durable=True)
                    channel.basic_qos(prefetch_count=1)
                    for event in keys:
                        queue = f"gamehub_{self.service}_{event.replace('.', '_')}"
                        channel.queue_declare(queue, durable=True)
                        channel.queue_bind(queue, EXCHANGE, event)
                        channel.basic_consume(queue, lambda ch, method, props, body:
                                              self.receive(ch, method, props, body, handle))
                    self.ready.set()
                    while not self.stop.is_set():
                        connection.process_data_events(time_limit=1)
            except Exception:
                logging.exception("RabbitMQ connection failed")
            finally:
                self.ready.clear()
            self.stop.wait(2)

    def receive(self, channel, method, properties, body, handle):
        event = method.routing_key
        try:
            data = json.loads(body)
            context = propagate.extract(properties.headers or {})
            with tracer.start_as_current_span(event, context=context, kind=SpanKind.CONSUMER):
                handle(event, data)
        except Exception:
            logging.exception("Could not process %s", event)
            self.stop.wait(1)
            channel.basic_nack(method.delivery_tag, requeue=not method.redelivered)
        else:
            channel.basic_ack(method.delivery_tag)

