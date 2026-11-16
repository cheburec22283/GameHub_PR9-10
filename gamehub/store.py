from contextlib import contextmanager

import psycopg
from psycopg.types.json import Jsonb


class Store:
    def __init__(self, url):
        self.url = url

    def initialize(self):
        with psycopg.connect(self.url) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS records (
                    kind text NOT NULL,
                    id text NOT NULL,
                    body jsonb NOT NULL,
                    PRIMARY KEY (kind, id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    event text NOT NULL,
                    payload jsonb NOT NULL,
                    headers jsonb NOT NULL
                )
            """)

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url) as conn:
            # Serialize writes and event delivery in this service's own database.
            conn.execute("SELECT pg_advisory_xact_lock(1)")
            yield Records(conn)


class Records:
    def __init__(self, conn):
        self.conn = conn

    def enqueue(self, event, payload, headers):
        self.conn.execute(
            "INSERT INTO outbox (event, payload, headers) VALUES (%s, %s, %s)",
            (event, Jsonb(payload), Jsonb(headers)),
        )

    def publish_pending(self, publish):
        for identifier, event, payload, headers in self.conn.execute(
            "SELECT id, event, payload, headers FROM outbox ORDER BY id LIMIT 100"
        ):
            publish(event, payload, headers)
            self.conn.execute("DELETE FROM outbox WHERE id=%s", (identifier,))

    def get(self, kind, identifier):
        row = self.conn.execute(
            "SELECT body FROM records WHERE kind=%s AND id=%s", (kind, identifier)
        ).fetchone()
        return row[0] if row else None

    def put(self, kind, identifier, body):
        self.conn.execute("""
            INSERT INTO records (kind, id, body) VALUES (%s, %s, %s)
            ON CONFLICT (kind, id) DO UPDATE SET body=EXCLUDED.body
        """, (kind, identifier, Jsonb(body)))

    def all(self, kind):
        rows = self.conn.execute("""
            SELECT body FROM records WHERE kind=%s
            ORDER BY COALESCE(body->>'created_at', body->>'issued_at', ''), id
        """, (kind,))
        return [row[0] for row in rows]

