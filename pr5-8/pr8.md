# Практическая работа №8. Тестирование сервисов

Листинги ниже — фрагменты из отчёта. Рабочая восстановленная версия находится в корне репозитория.


## Цель работы

Изучение видов pre-deployment тестирования и получение практических навыков написания автотестов.

## Выбор видов тестирования

Из трёх видов (unit-тесты, интеграционные и компонентные) по заданию выбраны два: unit-тесты и интеграционные тесты. Unit-тесты проверяют бизнес-объекты и бизнес-логику в полной изоляции, интеграционные — работу репозиториев с настоящей PostgreSQL. Все восемь сервисов покрыты тестами: всего написано 231 тест на pytest 8.

| Сервис | Unit: модели | Unit: сервис | Интеграционные | Всего |
| --- | --- | --- | --- | --- |
| user-service | 7 | 10 | 10 | 27 |
| publisher-service | 5 | 13 | 8 | 26 |
| catalog-service | 6 | 14 | 9 | 29 |
| license-service | 8 | 18 | 9 | 35 |
| order-service | 7 | 16 | 7 | 30 |
| payment-service | 5 | 14 | 10 | 29 |
| library-service | 4 | 10 | 10 | 24 |
| review-service | 8 | 15 | 8 | 31 |
| Итого | 50 | 110 | 71 | 231 |

Тесты лежат в папках tests/unit и tests/integration каждого сервиса. В pytest.ini задан pythonpath = ., поэтому тесты импортируют пакет app без дополнительной настройки.

## Unit-тесты

На unit-уровне работа с внешними зависимостями недопустима, поэтому все они подменяются моками. Благодаря внедрению зависимостей через конструктор сервис получает локальный репозиторий вместо репозитория БД, а вместо RabbitMQ и license-service — фейковые объекты.

```text
# order-service/tests/unit/fakes.py
class FakePublisher(EventPublisher):
    """Мок брокера: запоминает события вместо отправки в RabbitMQ."""
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, routing_key: str, payload: dict) -> None:
        self.events.append((routing_key, payload))

class FakeLicenseClient(LicenseClient):
    """Мок license-service: наличие ключей задаётся словарём в тесте."""
    def __init__(self) -> None:
        self.licenses: dict[UUID, LicenseInfo] = {}
        self.available = True

    def get_license(self, game_id: UUID) -> LicenseInfo:
        if not self.available:
            raise ConnectionError('license-service is unavailable')
        if game_id not in self.licenses:
            raise KeyError(game_id)
        return self.licenses[game_id]
```


Сначала тестируются бизнес-объекты: обязательные поля, запрещённые значения, вычисляемые поля.

```text
# order-service/tests/unit/test_order_model.py
def test_order_total(items: list[OrderItem]) -> None:
    order = Order(id=uuid4(), customer_id=uuid4(), items=items,
                  status=OrderStatuses.CREATED, created_at=datetime.now(timezone.utc))
    assert order.total == Decimal('4597.00')

def test_order_items_required() -> None:
    with pytest.raises(ValidationError):
        Order(id=uuid4(), customer_id=uuid4(), items=[],
              status=OrderStatuses.CREATED, created_at=datetime.now(timezone.utc))
```


Затем бизнес-логика, включая все исключения и опубликованные события. В отличие от методического примера, каждый тест получает новый экземпляр сервиса через фикстуру, поэтому тесты не зависят от порядка запуска.

```text
# order-service/tests/unit/test_order_service.py
@pytest.fixture()
def service(licenses: FakeLicenseClient, publisher: FakePublisher) -> OrderService:
    return OrderService(LocalOrderRepo(), licenses, publisher)

def test_create_order(service, publisher, customer_id, star_frontier, pixel_quest) -> None:
    order = service.create_order(customer_id, [
        CreateOrderItem(game_id=star_frontier, quantity=2),
        CreateOrderItem(game_id=pixel_quest, quantity=1)])
    assert order.status == OrderStatuses.CREATED
    assert order.items[0].price == Decimal('1999.00')   # цена взята из license-service
    assert order.total == Decimal('4597.00')
    routing_key, payload = publisher.events[0]
    assert routing_key == 'order.created'

def test_create_order_license_unavailable(service, licenses, customer_id, star_frontier) -> None:
    licenses.available = False
    with pytest.raises(ConnectionError):
        make_order(service, customer_id, star_frontier)

def test_cancel_completed_forbidden(service, customer_id, star_frontier) -> None:
    order = make_order(service, customer_id, star_frontier)
    service.confirm_order(order.id)
    service.complete_order(order.id)
    with pytest.raises(ValueError):
        service.cancel_order(order.id)
```


В license-service отдельно проверено правило «всё или ничего» при резервировании:

```text
# license-service/tests/unit/test_license_service.py
def test_reserve_not_enough_changes_nothing(service, star_frontier, pixel_quest) -> None:
    with pytest.raises(ValueError):
        service.reserve(uuid4(), [ReserveItem(game_id=star_frontier, quantity=1),
                                  ReserveItem(game_id=pixel_quest, quantity=3)])
    assert service.get_license(star_frontier).reserved == 0
    assert service.get_reservations() == []
```


Покрытые сценарии order-service и license-service: создание заказа и резерва, объединение одинаковых позиций, отсутствие и нехватка ключей, недоступность license-service, жизненный цикл заказа, запрещённые переходы статусов, возврат денег при отмене оплаченного заказа, автоотмена при неудачном резерве, фильтрация и пагинация, снятие и списание резерва, реакции на события брокера. В остальных сервисах: уникальность email и запрет самостоятельной регистрации администратора (user); проверка владельца через user-service (в том числе его недоступность), переходы статусов издателя и блокировка (publisher); публикация только у верифицированного издателя, недоступность publisher-service, поиск по названию, жанру и платформе, снятие игр при блокировке издателя (catalog); идемпотентное создание оплаты, подтверждение, отклонение, возврат и закрытие при отмене (payment); выдача ключа по событию, активация и аннулирование, запрет повторной активации (library); проверка факта покупки, запрет повторного и чужого отзыва, расчёт среднего рейтинга (review).

## Интеграционные тесты

Интеграционные тесты проверяют репозитории, работающие с PostgreSQL. Подключение берётся из переменной POSTGRES_URL. Фикстура с областью session создаёт таблицы один раз, а фикстура db после каждого теста очищает их, чтобы тесты были независимыми.

```text
# tests/integration/conftest.py
@pytest.fixture(scope='session')
def engine():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture()
def db(engine) -> Session:
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.rollback()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()

# order-service/tests/integration/test_order_repo_db.py
def test_create_and_get(repo: DbOrderRepo) -> None:
    order = new_order()
    assert repo.create_order(order) == order
    saved = repo.get_order(order.id)
    assert saved == order                  # позиции (JSON) и Decimal-цены восстановлены
    assert saved.total == Decimal('1999.00')

def test_filters_sorting_pagination(repo: DbOrderRepo) -> None:
    customer_id = uuid4()
    third, first, second = new_order(customer_id, 30), new_order(customer_id, 10), \
        new_order(customer_id, 20)
    for order in (third, first, second, new_order(minutes=40)):
        repo.create_order(order)
    assert [o.id for o in repo.get_orders(customer_id=customer_id)] == \
        [first.id, second.id, third.id]
    assert [o.id for o in repo.get_orders(customer_id=customer_id, offset=1, count=1)] == \
        [second.id]
```


В каждом сервисе дополнительно проверено, что бизнес-логика корректно работает поверх настоящей БД, и ограничения уникальности на уровне СУБД: email пользователя, одна оплата на заказ, один ключ библиотеки на позицию заказа, один отзыв на игру из заказа.

Локальный запуск:

```text
cd order-service
pip install -r requirements-dev.txt
pytest tests/unit
docker run -d --name pg-tests -e POSTGRES_PASSWORD=password -p 5432:5432 postgres:16
pytest tests/integration
```
