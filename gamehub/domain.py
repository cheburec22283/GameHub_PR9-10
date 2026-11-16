from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4


class DomainError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def require(condition, message, status=409):
    if not condition:
        raise DomainError(status, message)


def fetch(records, kind, key):
    item = records.get(kind, key)
    require(item is not None, f"{kind} not found", 404)
    return item


def create(records, kind, **fields):
    identifier = str(uuid4())
    time_field = "issued_at" if kind == "library" else "created_at"
    item = {"id": identifier, time_field: datetime.now(timezone.utc).isoformat(), **fields}
    records.put(kind, identifier, item)
    return item


def move(records, kind, identifier, allowed, status):
    item = fetch(records, kind, identifier)
    require(item["status"] in allowed, "Invalid status transition")
    item["status"] = status
    records.put(kind, identifier, item)
    return item


def merge_items(items):
    quantities = {}
    for item in items:
        key = item["game_id"]
        quantities[key] = quantities.get(key, 0) + item["quantity"]
    return quantities


def money(amount):
    return f"{Decimal(amount):.2f}"


def cancel_order(records, order_id, reason, emit):
    order = fetch(records, "orders", order_id)
    refund = order["status"] == "confirmed"
    order = move(records, "orders", order_id, {"created", "confirmed"}, "canceled")
    emit("order.canceled", order_id=order_id, reason=reason, refund_required=refund)
    return order


def reserve(records, order_id, items, emit):
    if records.get("reservations", order_id):
        return
    quantities = merge_items(items)
    licenses = {key: records.get("licenses", key) for key in quantities}
    if any(not licenses[key] or licenses[key]["available"] < amount
           for key, amount in quantities.items()):
        emit("license.reserve_failed", order_id=order_id, reason="insufficient keys")
        return
    for key, amount in quantities.items():
        license_ = licenses[key]
        license_["reserved"] += amount
        license_["available"] -= amount
        records.put("licenses", key, license_)
    records.put("reservations", order_id, {
        "order_id": order_id, "items": items, "status": "reserved"
    })
    emit("license.reserved", order_id=order_id)


def settle(records, order_id, consume):
    reservation = records.get("reservations", order_id)
    if not reservation or reservation["status"] != "reserved":
        return
    for key, amount in merge_items(reservation["items"]).items():
        license_ = fetch(records, "licenses", key)
        license_["reserved"] -= amount
        if consume:
            license_["quantity"] -= amount
        license_["available"] = license_["quantity"] - license_["reserved"]
        records.put("licenses", key, license_)
    reservation["status"] = "consumed" if consume else "released"
    records.put("reservations", order_id, reservation)


def react(service, records, event, data, emit):
    if service == "catalog":
        for game in records.all("games"):
            if game["publisher_id"] == data["publisher_id"] and game["status"] == "published":
                move(records, "games", game["id"], {"published"}, "unpublished")
                emit("catalog.game_unpublished", game_id=game["id"])
        return

    order_id = data["order_id"]
    if service == "license":
        if event == "order.created":
            reserve(records, order_id, data["items"], emit)
        else:
            settle(records, order_id, event == "order.confirmed")
    elif service == "payment":
        payment = next((entry for entry in records.all("payments")
                        if entry["order_id"] == order_id), None)
        if event == "order.created" and not payment:
            create(records, "payments", order_id=order_id, amount=data["total"], status="pending")
        elif event == "order.canceled" and payment:
            if payment["status"] == "confirmed" and data.get("refund_required"):
                move(records, "payments", payment["id"], {"confirmed"}, "refunded")
                emit("payment.refunded", order_id=order_id, payment_id=payment["id"])
            elif payment["status"] == "pending":
                move(records, "payments", payment["id"], {"pending"}, "failed")
    elif service == "order":
        order = fetch(records, "orders", order_id)
        if event == "payment.confirmed" and order["status"] == "created":
            move(records, "orders", order_id, {"created"}, "confirmed")
            emit("order.confirmed", order_id=order_id, customer_id=order["customer_id"],
                 items=order["items"])
        elif event == "library.issued" and order["status"] == "confirmed":
            move(records, "orders", order_id, {"confirmed"}, "completed")
        elif event in {"payment.failed", "license.reserve_failed"} and order["status"] == "created":
            cancel_order(records, order_id, data.get("reason", event), emit)
    elif service == "library":
        existing = {entry["game_id"] for entry in records.all("library")
                    if entry["order_id"] == order_id}
        for item in data["items"]:
            if item["game_id"] not in existing:
                create(records, "library", order_id=order_id, game_id=item["game_id"],
                       user_id=data["customer_id"], key=uuid4().hex[:16].upper(), status="issued")
        emit("library.issued", order_id=order_id)
