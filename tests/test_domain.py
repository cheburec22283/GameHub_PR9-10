import unittest

from gamehub.domain import DomainError, create, merge_items, move, react


class MemoryRecords:
    def __init__(self):
        self.items = {}

    def put(self, kind, key, item):
        self.items[kind, key] = item.copy()

    def get(self, kind, key):
        item = self.items.get((kind, key))
        return item.copy() if item else None

    def all(self, kind):
        return [value.copy() for (name, _), value in self.items.items() if name == kind]


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.records = MemoryRecords()
        self.events = []

    def emit(self, event, **data):
        self.events.append((event, data))

    def handle(self, service, event, **data):
        react(service, self.records, event, data, self.emit)

    def test_order_items_are_merged(self):
        self.assertEqual(merge_items([
            {"game_id": "g", "quantity": 1}, {"game_id": "g", "quantity": 2}
        ]), {"g": 3})

    def test_status_transition_validates_current_status(self):
        item = create(self.records, "publishers", status="pending")
        move(self.records, "publishers", item["id"], {"pending"}, "verified")
        with self.assertRaises(DomainError):
            move(self.records, "publishers", item["id"], {"pending"}, "verified")

    def test_blocking_publisher_unpublishes_only_their_games(self):
        target = create(self.records, "games", publisher_id="p", status="published")
        other = create(self.records, "games", publisher_id="q", status="published")
        self.handle("catalog", "publisher.blocked", publisher_id="p")
        self.assertEqual(self.records.get("games", target["id"])["status"], "unpublished")
        self.assertEqual(self.records.get("games", other["id"])["status"], "published")
        self.assertEqual(self.events, [("catalog.game_unpublished", {"game_id": target["id"]})])

    def test_reservation_is_atomic_and_idempotent(self):
        self.records.put("licenses", "g", {"quantity": 2, "available": 2, "reserved": 0})
        items = [{"game_id": "g", "quantity": 1}]
        self.handle("license", "order.created", order_id="bad",
                    items=[*items, {"game_id": "missing", "quantity": 1}])
        self.assertEqual(self.records.get("licenses", "g")["available"], 2)
        self.assertEqual(self.events[0][0], "license.reserve_failed")
        for _ in range(2):
            self.handle("license", "order.created", order_id="ok", items=items)
        self.assertEqual(self.records.get("licenses", "g")["reserved"], 1)
        self.handle("license", "order.confirmed", order_id="ok")
        self.assertEqual(self.records.get("licenses", "g"),
                         {"quantity": 1, "available": 1, "reserved": 0})

    def test_confirmation_updates_order_and_library(self):
        order = create(self.records, "orders", customer_id="u", status="created",
                       items=[{"game_id": "g", "quantity": 1}])
        self.handle("order", "payment.confirmed", order_id=order["id"])
        self.assertEqual(self.records.get("orders", order["id"])["status"], "confirmed")
        event, payload = self.events[0]
        self.handle("library", event, **payload)
        self.handle("library", event, **payload)
        self.assertEqual(len(self.records.all("library")), 1)
        self.handle("order", "library.issued", order_id=order["id"])
        self.assertEqual(self.records.get("orders", order["id"])["status"], "completed")

    def test_pending_payment_is_idempotent(self):
        for _ in range(2):
            self.handle("payment", "order.created", order_id="o", total="10.00")
        payment, = self.records.all("payments")
        self.assertEqual(payment["status"], "pending")
        self.handle("payment", "order.canceled", order_id="o", refund_required=False)
        self.assertEqual(self.records.all("payments")[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
