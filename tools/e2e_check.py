import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

PORTS = dict(zip(
    ("license", "order", "user", "publisher", "catalog", "payment", "library", "review"),
    range(8001, 8009),
))


def request(port, path, method="GET", data=None, expected=200):
    payload = json.dumps(data).encode() if data is not None else None
    req = Request(f"http://127.0.0.1:{port}{path}", data=payload, method=method,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as response:
            status, body = response.status, response.read()
    except HTTPError as error:
        status, body = error.code, error.read()
    assert status == expected, (method, path, status, body)
    return json.loads(body)


def api(service, path, method="GET", data=None, expected=200):
    return request(PORTS[service], f"/api/{path}", method, data, expected)


def wait_for(check, description, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except URLError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {description}")


def smoke():
    token = uuid4().hex
    owner = api("user", "users", "POST",
                {"email": f"owner-{token}@example.com", "name": "Studio", "role": "publisher"}, 201)
    buyer = api("user", "users", "POST",
                {"email": f"buyer-{token}@example.com", "name": "Buyer"}, 201)
    publisher = api("publisher", "publishers", "POST",
                    {"owner_id": owner["id"], "name": "Nova Games"}, 201)
    publisher_id = publisher["id"]
    game_body = {"publisher_id": publisher_id, "title": "Star Frontier", "genre": "Strategy",
                 "description": "Space strategy", "platforms": ["linux"]}
    api("catalog", "games", "POST", game_body, 409)
    api("publisher", f"publishers/{publisher_id}:verify", "POST")
    game = api("catalog", "games", "POST", game_body, 201)["id"]
    api("license", f"licenses/{game}", "PUT",
        {"publisher_id": publisher_id, "price": "1999.00", "quantity": 10})
    order = api("order", "orders", "POST", {"customer_id": buyer["id"],
                "items": [{"game_id": game, "quantity": 1}]}, 201)
    order_id = order["id"]
    assert order["total"] == "1999.00"
    wait_for(lambda: api("license", f"licenses/{game}")["reserved"] == 1, "license reservation")
    payments = wait_for(lambda: api("payment", f"payments?order_id={order_id}"), "payment")
    payment_id = payments[0]["id"]
    review = {"author_id": buyer["id"], "order_id": order_id, "game_id": game, "rating": 5}
    api("review", "reviews", "POST", review, 409)
    api("payment", f"payments/{payment_id}:confirm", "POST")
    api("payment", f"payments/{payment_id}:confirm", "POST", expected=409)
    wait_for(lambda: api("order", f"orders/{order_id}")["status"] == "completed", "completed order")
    wait_for(lambda: api("license", f"licenses/{game}")["quantity"] == 9, "license settlement")
    item, = api("library", f"library?order_id={order_id}")
    assert api("library", f"library/{item['id']}:activate", "POST")["status"] == "activated"
    api("review", "reviews", "POST", review, 201)
    api("review", "reviews", "POST", review, 409)
    assert api("review", f"ratings/{game}")["average"] == 5
    canceled = api("order", "orders", "POST", {"customer_id": buyer["id"],
                   "items": [{"game_id": game, "quantity": 2}]}, 201)["id"]
    wait_for(lambda: api("license", f"licenses/{game}")["reserved"] == 2, "second reservation")
    canceled_payment = wait_for(
        lambda: api("payment", f"payments?order_id={canceled}"), "second payment"
    )[0]["id"]
    assert api("order", f"orders/{canceled}:cancel", "POST")["status"] == "canceled"
    wait_for(lambda: api("license", f"licenses/{game}")["reserved"] == 0, "released reservation")
    wait_for(lambda: api("payment", f"payments/{canceled_payment}")["status"] == "failed",
             "closed payment")
    api("publisher", f"publishers/{publisher_id}:block", "POST")
    wait_for(lambda: api("catalog", f"games/{game}")["status"] == "unpublished", "unpublished game")
    print("Purchase scenario passed across all eight services")


def metrics_ready():
    path = "/api/v1/query?" + urlencode({"query": 'sum by (job) (gamehub_http_requests_total)'})
    result = request(9090, path)["data"]["result"]
    return {item["metric"]["job"] for item in result} == {f"{service}-service" for service in PORTS}


def trace_ready():
    path = "/api/traces?" + urlencode({"service": "payment-service", "limit": 30})
    traces = request(16686, path)["data"]
    expected = {"payment-service", "order-service", "license-service", "library-service"}
    for trace in traces:
        services = {process["serviceName"] for process in trace["processes"].values()}
        if expected <= services:
            return trace["traceID"]
    return None


if __name__ == "__main__":
    smoke()
    wait_for(metrics_ready, "Prometheus request data for all services")
    print("Prometheus collected HTTP metrics from all eight services")
    trace_id = wait_for(trace_ready, "Jaeger multi-service trace")
    print(f"Jaeger trace: http://localhost:16686/trace/{trace_id}")
