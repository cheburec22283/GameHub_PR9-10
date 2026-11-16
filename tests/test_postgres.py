import os
from uuid import uuid4

from fastapi.testclient import TestClient

from gamehub.web import app, kind, store


def test_service_reads_its_own_persisted_data():
    store.initialize()
    identifier = str(uuid4())
    item = {"id": identifier}
    if os.environ["SERVICE"] == "catalog":
        item["status"] = "published"
    with store.transaction() as records:
        records.put(kind, identifier, item)

    client = TestClient(app)
    response = client.get(f"/api/{kind}/{identifier}")
    assert response.status_code == 200
    assert response.json() == item
    assert item in client.get(f"/api/{kind}?count=100").json()
    assert client.get(f"/api/{kind}/{uuid4()}").status_code == 404
    assert client.get(f"/api/{kind}/invalid-uuid").status_code == 422
    assert "gamehub_http_requests_total" in client.get("/metrics").text


def test_transaction_rolls_back_state_and_event():
    store.initialize()
    identifier = str(uuid4())
    try:
        with store.transaction() as records:
            records.put(kind, identifier, {"id": identifier})
            records.enqueue("test.rollback", {"id": identifier}, {})
            raise RuntimeError("intentional rollback")
    except RuntimeError:
        pass
    pending = []
    with store.transaction() as records:
        assert records.get(kind, identifier) is None
        records.publish_pending(lambda *args: pending.append(args))
    assert pending == []
