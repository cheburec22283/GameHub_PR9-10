import asyncio
import logging
import os
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from inspect import Parameter, Signature
from typing import Annotated, Literal
from uuid import UUID

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from opentelemetry import propagate
from pydantic import BaseModel, Field

from .broker import Broker
from .domain import (
    DomainError, cancel_order, create, fetch, merge_items, money, move, react, require,
)
from .store import Store
from .telemetry import configure

SERVICE = os.environ["SERVICE"]
FLUSH_EVENTS_ON_REQUEST = os.getenv("FLUSH_EVENTS_ON_REQUEST") == "true"
store = Store(os.environ["POSTGRES_URL"])
broker = Broker(os.environ["AMQP_URL"], SERVICE)
RESOURCE = {
    "user": ("users", "user_id"),
    "publisher": ("publishers", "publisher_id"),
    "catalog": ("games", "game_id"),
    "license": ("licenses", "game_id"),
    "order": ("orders", "order_id"),
    "payment": ("payments", "payment_id"),
    "library": ("library", "item_id"),
    "review": ("reviews", "review_id"),
}
kind, identifier = RESOURCE[SERVICE]


@contextmanager
def change():
    with store.transaction() as records:
        def emit(event, **payload):
            headers = {}
            propagate.inject(headers)
            records.enqueue(event, payload, headers)

        yield records, emit
    if FLUSH_EVENTS_ON_REQUEST:
        flush_events()


def flush_events():
    try:
        with store.transaction() as records:
            records.publish_pending(broker.send)
    except Exception:
        logging.exception("Outbox publication failed")


def consume(event, data):
    with change() as (records, emit):
        react(SERVICE, records, event, data, emit)


@asynccontextmanager
async def lifespan(_):
    store.initialize()
    broker.start(consume)
    task = asyncio.create_task(deliver_events())
    try:
        yield
    finally:
        broker.stop.set()
        task.cancel()
        if provider:
            provider.force_flush()


async def deliver_events():
    while True:
        await asyncio.to_thread(flush_events)
        await asyncio.sleep(0.5)


app = FastAPI(title=f"{SERVICE}-service", lifespan=lifespan)


@app.exception_handler(DomainError)
async def domain_error(_, error):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


@app.get("/health")
def health():
    if not broker.ready.is_set():
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok"}


def upstream(service, resource, key):
    base = os.getenv(f"{service.upper()}_URL", f"http://{service}-service:8080")
    try:
        response = requests.get(f"{base}/api/{resource}/{key}", timeout=4)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise DomainError(503, f"{service}-service unavailable") from exc


@app.get(f"/api/{kind}")
def list_items(
    offset: Annotated[int, Query(ge=0)] = 0,
    count: Annotated[int, Query(ge=1, le=100)] = 20,
    role: str | None = None,
    status: str | None = None,
    owner_id: UUID | None = None,
    publisher_id: UUID | None = None,
    customer_id: UUID | None = None,
    order_id: UUID | None = None,
    user_id: UUID | None = None,
    game_id: UUID | None = None,
    q: str | None = None,
    genre: str | None = None,
    platform: str | None = None,
):
    filters = {
        "role": role, "status": status, "owner_id": owner_id, "publisher_id": publisher_id,
        "customer_id": customer_id, "order_id": order_id, "user_id": user_id,
        "game_id": game_id, "genre": genre,
    }
    if SERVICE == "catalog":
        filters["status"] = "published"
    with store.transaction() as records:
        result = records.all(kind)
    for key, value in filters.items():
        if value is not None:
            result = [item for item in result if item.get(key) == str(value)]
    if q is not None and SERVICE == "catalog":
        result = [item for item in result if q.casefold() in item["title"].casefold()]
    if platform is not None and SERVICE == "catalog":
        result = [item for item in result if platform.casefold() in item["platforms"]]
    return result[offset:offset + count]


def get_item(**parameters):
    with store.transaction() as records:
        return fetch(records, kind, str(parameters[identifier]))


get_item.__signature__ = Signature([Parameter(identifier, Parameter.KEYWORD_ONLY, annotation=UUID)])
app.get(f"/api/{kind}/{{{identifier}}}")(get_item)


class UserBody(BaseModel):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    name: str = Field(min_length=1)
    role: Literal["buyer", "publisher", "admin"] = "buyer"


class PublisherBody(BaseModel):
    owner_id: UUID
    name: str = Field(min_length=1)


class GameBody(BaseModel):
    publisher_id: UUID
    title: str = Field(min_length=1)
    genre: str
    description: str = Field(min_length=1)
    platforms: list[Literal["windows", "linux", "macos"]] = Field(min_length=1)
    min_requirements: str | None = None


class LicenseBody(BaseModel):
    publisher_id: UUID
    price: Decimal = Field(gt=0, decimal_places=2)
    quantity: int = Field(ge=0)


class OrderItem(BaseModel):
    game_id: UUID
    quantity: int = Field(gt=0)


class OrderBody(BaseModel):
    customer_id: UUID
    items: list[OrderItem] = Field(min_length=1)


class CancelBody(BaseModel):
    reason: str | None = None


class ReviewBody(BaseModel):
    order_id: UUID
    game_id: UUID
    author_id: UUID
    rating: int = Field(ge=1, le=5)
    text: str | None = None


if SERVICE == "user":
    @app.post("/api/users", status_code=201)
    def register(body: UserBody):
        require(body.role != "admin", "Cannot register an administrator")
        with change() as (records, emit):
            require(all(user["email"].casefold() != body.email.casefold()
                        for user in records.all("users")), "Email already in use")
            user = create(records, "users", **body.model_dump(), is_blocked=False)
            emit("user.registered", user_id=user["id"], role=user["role"])
            return user

    @app.post("/api/users/{user_id}:block")
    def block_user(user_id: UUID):
        with change() as (records, emit):
            user = fetch(records, "users", str(user_id))
            require(not user["is_blocked"] and user["role"] != "admin", "Cannot block user")
            user["is_blocked"] = True
            records.put("users", user["id"], user)
            emit("user.blocked", user_id=user["id"])
            return user


if SERVICE == "publisher":
    @app.post("/api/publishers", status_code=201)
    def create_publisher(body: PublisherBody):
        owner = upstream("user", "users", body.owner_id)
        require(owner and owner["role"] == "publisher" and not owner["is_blocked"],
                "Owner must have an active publisher account")
        with change() as (records, emit):
            require(all(pub["owner_id"] != str(body.owner_id) or pub["name"] != body.name
                        for pub in records.all("publishers")), "Publisher already exists")
            pub = create(records, "publishers", **body.model_dump(mode="json"), status="pending")
            emit("publisher.created", publisher_id=pub["id"], owner_id=pub["owner_id"])
            return pub

    @app.post("/api/publishers/{publisher_id}:verify")
    def verify_publisher(publisher_id: UUID):
        with change() as (records, emit):
            item = move(records, "publishers", str(publisher_id), {"pending"}, "verified")
            emit("publisher.verified", publisher_id=item["id"])
            return item

    @app.post("/api/publishers/{publisher_id}:block")
    def block_publisher(publisher_id: UUID):
        with change() as (records, emit):
            item = move(records, "publishers", str(publisher_id), {"pending", "verified"}, "blocked")
            emit("publisher.blocked", publisher_id=item["id"])
            return item


if SERVICE == "catalog":
    @app.post("/api/games", status_code=201)
    def publish_game(body: GameBody):
        publisher = upstream("publisher", "publishers", body.publisher_id)
        require(publisher and publisher["status"] == "verified", "Publisher must be verified")
        with change() as (records, emit):
            item = create(records, "games", **body.model_dump(mode="json"), status="published")
            emit("catalog.game_published", game_id=item["id"], publisher_id=item["publisher_id"])
            return item

    @app.post("/api/games/{game_id}:unpublish")
    def unpublish_game(game_id: UUID):
        with change() as (records, emit):
            item = move(records, "games", str(game_id), {"published"}, "unpublished")
            emit("catalog.game_unpublished", game_id=item["id"])
            return item


if SERVICE == "license":
    @app.put("/api/licenses/{game_id}")
    def update_license(game_id: UUID, body: LicenseBody):
        key = str(game_id)
        with store.transaction() as records:
            reserved = (records.get("licenses", key) or {}).get("reserved", 0)
            require(body.quantity >= reserved, "Quantity cannot be below reserved keys")
            item = {"game_id": key, "publisher_id": str(body.publisher_id),
                    "price": money(body.price), "quantity": body.quantity, "reserved": reserved,
                    "available": body.quantity - reserved}
            records.put("licenses", key, item)
            return item

    @app.get("/api/reservations")
    def list_reservations(order_id: UUID | None = None):
        with store.transaction() as records:
            return [item for item in records.all("reservations")
                    if order_id is None or item["order_id"] == str(order_id)]


if SERVICE == "order":
    @app.post("/api/orders", status_code=201)
    def place_order(body: OrderBody):
        items = []
        for game_id, quantity in merge_items(body.model_dump(mode="json")["items"]).items():
            license_ = upstream("license", "licenses", game_id)
            require(license_ and license_["available"] >= quantity, "Insufficient keys", 400)
            items.append({"game_id": game_id, "quantity": quantity, "price": license_["price"]})
        total = money(sum(Decimal(item["price"]) * item["quantity"] for item in items))
        with change() as (records, emit):
            order = create(records, "orders", customer_id=str(body.customer_id), items=items,
                           total=total, status="created")
            emit("order.created", order_id=order["id"], customer_id=order["customer_id"],
                 items=items, total=total)
            return order

    @app.post("/api/orders/{order_id}:cancel")
    def cancel(order_id: UUID, body: CancelBody | None = None):
        with change() as (records, emit):
            return cancel_order(records, str(order_id), body.reason if body else None, emit)


if SERVICE == "payment":
    def payment_action(payment_id, status):
        with change() as (records, emit):
            item = move(records, "payments", str(payment_id), {"pending"}, status)
            emit(f"payment.{status}", order_id=item["order_id"], payment_id=item["id"])
            return item

    @app.post("/api/payments/{payment_id}:confirm")
    def confirm(payment_id: UUID):
        return payment_action(payment_id, "confirmed")

    @app.post("/api/payments/{payment_id}:fail")
    def fail(payment_id: UUID):
        return payment_action(payment_id, "failed")


if SERVICE == "library":
    @app.post("/api/library/{item_id}:activate")
    def activate(item_id: UUID):
        with change() as (records, emit):
            item = move(records, "library", str(item_id), {"issued"}, "activated")
            emit("library.activated", item_id=item["id"], game_id=item["game_id"])
            return item

    @app.post("/api/library/{item_id}:revoke")
    def revoke(item_id: UUID):
        with store.transaction() as records:
            return move(records, "library", str(item_id), {"issued", "activated"}, "revoked")


if SERVICE == "review":
    @app.post("/api/reviews", status_code=201)
    def create_review(body: ReviewBody):
        order = upstream("order", "orders", body.order_id)
        data = body.model_dump(mode="json")
        require(order and order["customer_id"] == data["author_id"]
                and order["status"] == "completed"
                and any(item["game_id"] == data["game_id"] for item in order["items"]),
                "Completed purchase required")
        with store.transaction() as records:
            require(all(item["order_id"] != data["order_id"] or item["game_id"] != data["game_id"]
                        for item in records.all("reviews")), "Review already exists")
            return create(records, "reviews", **data)

    @app.get("/api/ratings/{game_id}")
    def rating(game_id: UUID):
        with store.transaction() as records:
            ratings = [item["rating"] for item in records.all("reviews")
                       if item["game_id"] == str(game_id)]
        return {"game_id": str(game_id), "count": len(ratings),
                "average": round(sum(ratings) / len(ratings), 2) if ratings else None}


provider = configure(app, f"{SERVICE}-service")
