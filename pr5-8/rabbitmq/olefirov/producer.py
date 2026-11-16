# olefirov/producer.py
import sys

import pika

from common import connect, setup, EXCHANGE, QUEUE_DURABLE

DEFAULT_KEY = 'catalog.game_published'
DEFAULT_TEXT = 'Игра 17'

def main() -> None:
    routing_key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KEY
    text = ' '.join(sys.argv[2:]) or DEFAULT_TEXT
    connection = connect()
    channel = connection.channel()
    setup(channel)
    properties = None
    if QUEUE_DURABLE:
        # persistent-сообщение переживает перезапуск брокера вместе с durable-очередью
        properties = pika.BasicProperties(delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE)
    channel.basic_publish(exchange=EXCHANGE, routing_key=routing_key, body=text.encode(),
                          properties=properties)
    print(f'SENT [{EXCHANGE}, key="{routing_key}"]: {text}')
    connection.close()

if __name__ == '__main__':
    main()
