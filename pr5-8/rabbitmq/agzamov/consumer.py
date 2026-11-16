# agzamov/consumer.py
from time import sleep

from common import connect, setup, QUEUE, EXCHANGE, EXCHANGE_TYPE, BINDING_KEY

def get_delay(body: bytes) -> int:
    """Вариант 4: задержка — число секунд, переданное в теле сообщения."""
    return int(body.decode())

def process_message(channel, method, properties, body: bytes) -> None:
    print(f' [x] Received [{method.routing_key}] {body.decode()}')
    try:
        delay = get_delay(body)
    except ValueError:
        print(' [!] Тело сообщения не является числом, сообщение отклонено')
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    sleep(delay)  # имитация ресурсоёмкой задачи
    print(' [x] Done')
    channel.basic_ack(delivery_tag=method.delivery_tag)  # ручное подтверждение

def main() -> None:
    connection = connect()
    channel = connection.channel()
    setup(channel)
    channel.basic_qos(prefetch_count=1)  # не запрашивать сообщения заранее
    channel.basic_consume(queue=QUEUE, on_message_callback=process_message)
    print(f'{EXCHANGE_TYPE} exchange {EXCHANGE}, queue {QUEUE}, binding "{BINDING_KEY}". CTRL+C to exit')
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        connection.close()

if __name__ == '__main__':
    main()
