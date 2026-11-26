import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    token = os.environ["YC_IAM_TOKEN"]
    opener = build_opener(NoRedirect())
    base = os.getenv("YC_CONTAINER_URL", "").rstrip("/")
    if not base:
        identifier = quote(os.environ["YC_CONTAINER_ID"], safe="")
        endpoint = (
            "https://serverless-containers.api.cloud.yandex.net"
            f"/containers/v1/containers/{identifier}"
        )
        req = Request(endpoint, headers={"Authorization": f"Bearer {token}"})
        with opener.open(req, timeout=30) as response:
            base = json.load(response)["url"].rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname:
        raise ValueError("YC_CONTAINER_URL must be an HTTPS URL")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError(
            "YC_CONTAINER_URL must not contain credentials or query"
        )

    def request(path, expected, payload=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, data=body, headers=headers)
        try:
            with opener.open(req, timeout=60) as response:
                status = response.status
                result = response.read().decode()
        except HTTPError as error:
            status = error.code
            result = ""
        if status not in expected:
            raise RuntimeError(
                f"{req.get_method()} {path}: unexpected HTTP {status}"
            )
        return result

    request("/health", {401, 403}, authenticated=False)
    print("PASS: unauthenticated invocation is denied", flush=True)
    deadline = time.monotonic() + 180
    while True:
        try:
            result = json.loads(request("/health", {200}))
            if result.get("status") != "ok":
                raise RuntimeError("Application is not healthy")
            break
        except (RuntimeError, URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Health check did not pass within 180 seconds"
                )
            time.sleep(5)
    print("PASS: authenticated health check", flush=True)
    email = f"yc-smoke-{uuid4().hex}@example.invalid"
    user = json.loads(request("/api/users", {201}, {
        "email": email,
        "name": "Yandex Cloud smoke test",
        "role": "buyer",
    }))
    if not user.get("id") or user.get("email") != email:
        raise RuntimeError("Registration returned an unexpected record")
    identifier = user["id"]
    saved = json.loads(request(f"/api/users/{identifier}", {200}))
    if saved.get("id") != identifier or saved.get("email") != email:
        raise RuntimeError("Registered user was not persisted")
    offset = 0
    while True:
        batch = json.loads(request(
            f"/api/users?offset={offset}&count=100", {200}
        ))
        if not isinstance(batch, list):
            raise RuntimeError("User list returned an unexpected response")
        if any(item.get("id") == identifier for item in batch):
            break
        if len(batch) < 100:
            raise RuntimeError(
                "Registered user was not returned by the user list"
            )
        offset += 100
    print("PASS: registration, persisted record and user list", flush=True)
    metrics = request("/metrics", {200})
    if "gamehub_http_requests_total" not in metrics:
        raise RuntimeError("HTTP metrics were not exposed")
    print("PASS: HTTP metrics", flush=True)
    print(json.dumps({"status": "passed", "user_id": identifier}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
