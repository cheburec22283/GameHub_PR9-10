# Практическая работа №7. Создание микросервисов

Листинги ниже — фрагменты из отчёта. Рабочая восстановленная версия находится в корне репозитория.


## Цель работы

Приобретение практических навыков разработки backend-приложений для системы микросервисов и обеспечения межсервисного взаимодействия на примере созданных приложений.

## Архитектура системы

Реализованы все восемь микросервисов из ПР3–4: user-service, publisher-service, catalog-service, license-service, order-service, payment-service, library-service и review-service. Все написаны на FastAPI (Python 3.12), каждый хранит данные в собственной PostgreSQL (принцип database per service), обмениваются событиями через RabbitMQ и упакованы в отдельные Docker-контейнеры. Внешняя система — эквайринг — эмулируется методами payment-service (:confirm, :fail). Отличие от ПР4: для каталога, лицензионных ключей и отзывов вместо MongoDB/Elasticsearch/Redis использована PostgreSQL (список платформ и поиск хранятся в JSON-колонке и фильтруются в приложении), чтобы сохранить единый стек.

Клиенты обращаются к сервисам по HTTP. Четыре синхронные связи: publisher-service проверяет владельца в user-service, catalog-service проверяет издателя в publisher-service, order-service запрашивает наличие ключей у license-service, review-service проверяет факт покупки в order-service. Резервирование, оплата, выдача ключа, снятие игр с публикации и смена статусов заказа идут событиями через RabbitMQ.

Выполнение требований задания:

| Требование | Как выполнено |
| --- | --- |
| Минимум два собственных микросервиса у каждого студента | Каждый из четырёх участников реализует по два сервиса, всего восемь из ПР4: user, publisher, catalog, license, order, payment, library, review |
| Вызов методов другого сервиса (по одному на студента) | publisher → user (Расулов), catalog → publisher (Измайлов), order → license (Агзаимов), review → order (Олефиров) |
| От двух до пяти эндпоинтов в каждом | publisher-service — 5, остальные семь — по 4 |
| Вложенные функции в эндпоинте | POST /api/orders → OrderService.create_order → _merge_items и _build_item → HTTP-вызов license-service; аналогично POST /api/publishers → _check_owner, POST /api/games → _check_publisher и POST /api/reviews → _check_purchase |
| Одна предметная область | Онлайн-платформа продажи компьютерных игр, все восемь контекстов из ПР3 |
| Работа с БД (минимум один сервис) | Все восемь сервисов работают с собственной PostgreSQL через SQLAlchemy 2.0, схема создаётся миграциями Alembic |
| Docker и Docker Compose | Dockerfile в каждом сервисе, общий docker-compose.yaml |

Таблица 5 — Распределение сервисов между участниками

| Участник | Сервисы | Эндпоинты | Подписки на сообщения | Порты Swagger |
| --- | --- | --- | --- | --- |
| Расулов Р.Б. | user-service, publisher-service | 4 + 5 | нет | 8003, 8004 |
| Измайлов Б.А. | catalog-service, license-service | 4 + 4 | publisher.blocked; order.created, order.canceled, order.confirmed | 8005, 8001 |
| Агзамов И.И. | order-service, payment-service | 4 + 4 | payment.confirmed, payment.failed, license.reserve_failed, library.issued; order.created, order.canceled | 8002, 8006 |
| Олефиров Г.Г. | library-service, review-service | 4 + 4 | order.confirmed; нет | 8007, 8008 |

## Структура проекта

```text
gamehub/
├── docker-compose.yaml                # 8 сервисов + 8 PostgreSQL + RabbitMQ
├── tools/e2e_check.py                 # сквозная проверка всей цепочки
├── tools/publish_event.py             # ручная публикация события (отладка)
├── <сервис>/                          # user, publisher, catalog, license, order, payment, library, review
│   ├── Dockerfile, requirements.txt, pytest.ini, alembic.ini
│   ├── alembic/                       # миграции БД (env.py, versions/)
│   ├── app/
│   │   ├── main.py                    # приложение FastAPI (+ запуск consumer, если есть подписки)
│   │   ├── settings.py                # конфигурация (pydantic-settings, .env)
│   │   ├── database.py                # подключение к PostgreSQL
│   │   ├── dependencies.py            # сборка сервиса (dependency injection)
│   │   ├── rabbitmq.py                # подписка на события (сервисы-потребители)
│   │   ├── models/                    # бизнес-объекты (pydantic)
│   │   ├── schemas/                   # таблицы БД (SQLAlchemy)
│   │   ├── repositories/              # base / local / db репозитории
│   │   ├── services/                  # бизнес-логика
│   │   ├── clients/                   # publisher.py, клиенты соседних сервисов
│   │   └── endpoints/                 # HTTP-интерфейс
│   └── tests/unit, tests/integration  # ПР8
```


## Подготовка проекта

Для каждого сервиса создаётся отдельное виртуальное окружение, в которое устанавливаются необходимые библиотеки; зависимости фиксируются в requirements.txt.

```text
python -m venv venv
./venv/Scripts/Activate.ps1   # для Windows
pip install fastapi "uvicorn[standard]" alembic pydantic-settings aio_pika pika sqlalchemy psycopg2-binary requests
pip freeze > requirements.txt
```


## Бизнес-логика

Как и в методическом примере, используются анемичные модели pydantic, а поведение вынесено в сервисы. Модель заказа хранит позиции с ценой, зафиксированной на момент оформления, и вычисляет итоговую сумму.

```text
# order-service/app/models/order.py
class OrderStatuses(enum.Enum):
    CREATED = 'created'        # заказ оформлен покупателем
    CONFIRMED = 'confirmed'    # оплата подтверждена
    COMPLETED = 'completed'    # ключ выдан в библиотеку
    CANCELED = 'canceled'      # заказ отменён

class OrderItem(BaseModel):
    game_id: UUID
    quantity: int = Field(gt=0, le=5)
    price: Decimal = Field(gt=0, decimal_places=2)

class Order(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_assignment=True)
    id: UUID
    customer_id: UUID
    items: list[OrderItem] = Field(min_length=1)
    status: OrderStatuses
    created_at: datetime

    @computed_field
    @property
    def total(self) -> Decimal:
        return sum((item.price * item.quantity for item in self.items), Decimal('0'))
```


Модель наличия ключей аналогично вычисляет доступное количество available = quantity − reserved.

Остальные шесть сервисов построены по той же схеме. Правила их предметной области: пользователь с ролью администратора не регистрируется самостоятельно, email уникален (user-service); издатель проходит статусы pending → verified → blocked, а блокировка снимает с публикации все его игры (publisher-service, catalog-service); игра публикуется только у верифицированного издателя; оплата создаётся по событию заказа, неоплаченная закрывается при отмене, оплаченная возвращается (payment-service); ключ выдаётся в библиотеку по событию подтверждения заказа и проходит issued → activated → revoked (library-service); отзыв допустим только на купленную и выданную игру автора и только один на игру (review-service). Вложенные синхронные вызовы вынесены во вспомогательные функции:

```text
# catalog-service/app/services/catalog_service.py
    def _check_publisher(self, publisher_id: UUID) -> None:
        try:
            publisher = self.publisher_client.get_publisher(publisher_id)
        except KeyError:
            raise ValueError(f'Издатель {publisher_id} не найден')
        if publisher.status != 'verified':
            raise ValueError(f'Издатель {publisher_id} не верифицирован (статус: {publisher.status})')

# review-service/app/services/review_service.py
    def _check_purchase(self, order_id: UUID, game_id: UUID, author_id: UUID) -> None:
        try:
            order = self.order_client.get_order(order_id)
        except KeyError:
            raise ValueError(f'Заказ {order_id} не найден')
        if order.customer_id != author_id:
            raise ValueError('Заказ оформлен другим покупателем')
        if order.status != 'completed':
            raise ValueError(f'Отзыв можно оставить только на выданную игру (статус: {order.status})')
        if game_id not in order.game_ids:
            raise ValueError(f'Игры {game_id} нет в заказе {order_id}')
```


```text
# publisher-service/app/services/publisher_service.py
    def _check_owner(self, owner_id: UUID) -> None:
        try:
            user = self.user_client.get_user(owner_id)         # Sync REST: publisher -> user
        except KeyError:
            raise ValueError(f'Пользователь {owner_id} не найден')
        if user.role != 'publisher':
            raise ValueError(f'Пользователь {owner_id} не является издателем (роль: {user.role})')
        if user.is_blocked:
            raise ValueError(f'Пользователь {owner_id} заблокирован')
```


Сервис заказов получает репозиторий, клиент license-service и издателя событий через конструктор. Это паттерн Dependency Injection: в тестах вместо БД и брокера подставляются локальные реализации.

```text
# order-service/app/services/order_service.py
class OrderService:
    def __init__(self, order_repo: BaseOrderRepo, licenses: LicenseClient,
                 publisher: EventPublisher | None = None) -> None: ...

    def create_order(self, customer_id, items) -> Order:
        # вложенные вызовы: объединение позиций -> запрос в license-service по каждой
        order_items = [self._build_item(game_id, qty)
                       for game_id, qty in self._merge_items(items).items()]
        order = Order(id=uuid4(), customer_id=customer_id, items=order_items,
                      status=OrderStatuses.CREATED, created_at=datetime.now(timezone.utc))
        self.order_repo.create_order(order)
        self._publish('order.created', {...})
        return order

    def _build_item(self, game_id: UUID, quantity: int) -> OrderItem:
        try:
            stock = self.licenses.get_license(game_id)      # Sync REST
        except KeyError:
            raise ValueError(f'Игра {game_id} не найдена')
        if stock.available < quantity:
            raise ValueError(f'Недостаточно ключей для игры {game_id}: доступно {stock.available}')
        return OrderItem(game_id=game_id, quantity=quantity, price=stock.price)

    def _change_status(self, order_id, allowed, new_status) -> Order:
        order = self.order_repo.get_order(order_id)
        if order.status not in allowed:
            raise ValueError(...)
        order.status = new_status
        return self.order_repo.set_status(order)
```


Переходы статусов (confirm_order, complete_order, cancel_order) реализованы через общий метод _change_status. В license-service метод reserve работает по принципу «всё или ничего»: сначала проверяются все позиции заказа, и только потом изменяются остатки ключей.

## Управление данными

Для каждого агрегата описан абстрактный репозиторий и две реализации: локальная (в памяти, для unit-тестов) и работающая с PostgreSQL. Бизнес-логика не знает, где хранятся данные.

```text
# order-service/app/schemas/order.py
class Order(Base):
    __tablename__ = 'orders'
    id = Column(Uuid, primary_key=True)
    customer_id = Column(Uuid, nullable=False, index=True)
    items = Column(JSON, nullable=False)          # [{game_id, quantity, price}]
    status = Column(Enum(OrderStatuses), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

# order-service/app/repositories/db_order_repo.py
def get_orders(self, customer_id=None, status=None, offset=0, count=None) -> list[Order]:
    query = select(DBOrder).order_by(DBOrder.created_at)
    if customer_id is not None:
        query = query.where(DBOrder.customer_id == customer_id)
    if status is not None:
        query = query.where(DBOrder.status == status)
    query = query.offset(offset)
    if count is not None:
        query = query.limit(count)
    return [Order.model_validate(o) for o in self.db.scalars(query).all()]
```


```text
# order-service/app/repositories/local_order_repo.py  (локальное хранилище для unit-тестов)
_orders: list[Order] = []

class OrderRepo(BaseOrderRepo):
    def get_order(self, order_id: UUID) -> Order:
        for o in _orders:
            if o.id == order_id:
                return o
        raise KeyError(order_id)

    def create_order(self, order: Order) -> Order:
        if any(o.id == order.id for o in _orders):
            raise KeyError(f'Order {order.id} already exists')
        _orders.append(order)
        return order
```


Оба репозитория реализуют один интерфейс, поэтому переход от памяти к базе данных не затрагивает ни модели, ни сервисы, ни контроллеры. license-service хранит таблицы licenses и reservations. Схема базы создаётся миграциями Alembic: файл alembic/env.py берёт адрес БД из настроек и метаданные Base.metadata, а контейнер перед стартом приложения выполняет alembic upgrade head.

```text
# order-service/alembic/versions/0001_create_orders.py
def upgrade() -> None:
    op.create_table(
        'orders',
        sa.Column('id', sa.Uuid, primary_key=True),
        sa.Column('customer_id', sa.Uuid, nullable=False, index=True),
        sa.Column('items', sa.JSON, nullable=False),
        sa.Column('status', sa.Enum('created', 'confirmed', 'completed', 'canceled', name='orderstatuses'),
                  nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table('orders')
```


## Интерфейсы взаимодействия

HTTP-интерфейс реализован роутерами FastAPI по спецификации из ПР6. Исключения бизнес-логики переводятся в коды ответа.

```text
# order-service/app/endpoints/order_router.py
@order_router.post('', status_code=201)
def create_order(body: CreateOrderRequest,
                 service: OrderService = Depends(get_order_service)) -> Order:
    try:
        return service.create_order(body.customer_id, body.items)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ConnectionError as e:
        raise HTTPException(503, str(e))

@order_router.post('/{order_id}:cancel')
def cancel_order(order_id: UUID, body: CancelOrderRequest | None = None,
                 service: OrderService = Depends(get_order_service)) -> Order:
    try:
        return service.cancel_order(order_id, body.reason if body else None)
    except KeyError:
        raise HTTPException(404, f'Order with id={order_id} not found')
    except ValueError as e:
        raise HTTPException(409, str(e))
```


Синхронный вызов license-service вынесен в клиент с таймаутом; недоступность сервиса превращается в ConnectionError, а затем в ответ 503.

```text
# order-service/app/clients/license_client.py
def get_license(self, game_id: UUID) -> LicenseInfo:
    try:
        response = requests.get(f'{self.base_url}/api/licenses/{game_id}', timeout=self.timeout)
    except requests.RequestException as e:
        raise ConnectionError(f'license-service is unavailable: {e}') from e
    if response.status_code == 404:
        raise KeyError(game_id)
    if not response.ok:
        raise ConnectionError(f'license-service responded {response.status_code}')
    return LicenseInfo.model_validate(response.json())
```


Асинхронный обмен построен на навыках ПР5: события публикуются в durable topic-обменник gamehub как persistent-сообщения (pika), а каждый сервис читает свои durable-очереди через aio-pika. Подтверждение отправляется только после обработки; синхронная работа с БД вынесена в отдельный поток, чтобы не блокировать цикл событий.

```text
# license-service/app/rabbitmq.py
SUBSCRIPTIONS = {
    'order.created': LicenseService.handle_order_created,      # -> зарезервировать ключи
    'order.canceled': LicenseService.handle_order_canceled,    # -> снять резерв
    'order.confirmed': LicenseService.handle_order_confirmed,  # -> списать ключи после оплаты
}

async def consume() -> AbstractRobustConnection:
    connection = await connect_robust(settings.amqp_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    exchange = await channel.declare_exchange(settings.exchange, ExchangeType.TOPIC, durable=True)
    for routing_key, handler in SUBSCRIPTIONS.items():
        queue_name = f'{settings.queue_prefix}_license_{routing_key.replace(".", "_")}'
        queue = await channel.declare_queue(queue_name, durable=True)
        await queue.bind(exchange, routing_key=routing_key)
        await queue.consume(_make_consumer(handler))
    return connection
```


Если резерв не удался (последние ключи успели купить), license-service публикует license.reserve_failed, и order-service автоматически отменяет заказ — так реализована компенсация в цепочке событий (хореографическая сага). После подтверждения оплаты order-service публикует order.confirmed, library-service выдаёт ключ и публикует library.issued, по которому заказ переходит в completed.

Конфигурация читается из переменных окружения или файла .env через pydantic-settings (POSTGRES_URL, AMQP_URL, LICENSE_URL). Чтение очередей запускается вместе с приложением в обработчике startup; если RabbitMQ недоступен, HTTP API продолжает работать.

```text
# license-service/app/main.py
import asyncio

from fastapi import FastAPI

from app import rabbitmq
from app.endpoints.license_router import router as license_router

app = FastAPI(title='License Service')

@app.on_event('startup')
async def startup() -> None:
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(rabbitmq.consume(loop))

app.include_router(license_router, prefix='/api')
```


## Контейнеризация

```text
FROM python:3.12-slim
WORKDIR /code
COPY ./requirements.txt /code/
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
COPY ./app /code/app
COPY ./alembic.ini /code/alembic.ini
COPY ./alembic /code/alembic
EXPOSE 80
CMD ["/bin/sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 80"]
```


```text
  order-service:
    build: ./order-service
    ports:
      - 8002:80
    environment:
      POSTGRES_URL: postgresql://postgres:password@order-db:5432/postgres
      AMQP_URL: amqp://guest:guest@rabbitmq:5672/
      LICENSE_URL: http://license-service:80
    depends_on:
      order-db:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
      license-service:
        condition: service_started
```


Вся система поднимается командой docker compose up --build. Swagger UI: license-service — http://localhost:8001/docs, order-service — :8002, user-service — :8003, publisher-service — :8004, catalog-service — :8005, payment-service — :8006, library-service — :8007, review-service — :8008 (адрес вида http://localhost:<порт>/docs).

## Проверка работоспособности

- POST /api/users в user-service: регистрация покупателя и издателя; POST /api/publishers в publisher-service: издатель в статусе pending (синхронный вызов publisher → user; владелец-покупатель или заблокированный → 409); публикация игры у неверифицированного издателя → 409; после :verify → 201 (синхронный вызов catalog → publisher).

- PUT /api/licenses/{game_id} в license-service: издатель загружает 10 ключей; POST /api/orders в order-service: покупатель заказывает одну копию. Ответ 201, статус created, сумма 1999.00.

- GET /api/licenses/{game_id}: reserved = 1, available = 9 — резерв создан по событию order.created; payment-service по тому же событию создал оплату в статусе pending.

- POST /api/payments/{id}:confirm (эквайринг) → заказ confirmed по событию payment.confirmed; повторное подтверждение → 409.

- По событию order.confirmed library-service выдаёт ключ (статус issued), license-service списывает резерв; по событию library.issued заказ становится completed; POST /api/library/{id}:activate → activated.

- POST /api/reviews в review-service: отзыв до выдачи ключа → 409, после выдачи → 201 (синхронный вызов review → order), повторный отзыв → 409, GET /api/ratings/{game_id} → средняя оценка. Заказ сверх количества ключей → 400; отмена оплаченного заказа до выдачи ключа → резерв снят, оплата refunded; блокировка издателя → его игры сняты с публикации.

Вся цепочка автоматизирована скриптом tools/e2e_check.py: он проходит регистрацию, издателя, каталог, ключи, заказ, оплату, выдачу ключа, активацию и отзыв, затем проверяет отмену оплаченного заказа и блокировки, а также все ожидаемые отказы (409, 400, 503); асинхронные результаты ждёт с таймаутом. Все 44 проверки проходят.

## Вывод

Разработана микросервисная система из всех восьми сервисов онлайн-платформы продажи компьютерных игр, выделенных в ПР3–4, с собственными базами данных, четырьмя синхронными REST-связями и асинхронным обменом событиями через RabbitMQ: игра проходит путь от публикации в каталоге до заказа, оплаты, выдачи ключа в библиотеку и отзыва. Слоистая структура (модели, репозитории, сервисы, интерфейсы) и внедрение зависимостей позволяют менять хранилище и внешние системы без изменения бизнес-логики. Система упакована в 17 Docker-контейнеров и запускается одной командой Docker Compose.
