import os
from time import perf_counter

from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

LABELS = ("method", "route", "status")
REQUESTS = Counter("gamehub_http_requests_total", "HTTP requests", LABELS)
LATENCY = Histogram("gamehub_http_request_duration_seconds", "HTTP request time", LABELS)


def configure(app, service):
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    provider = None
    if endpoint:
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        RequestsInstrumentor().instrument()
        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health")

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.middleware("http")
    async def measure(request, call_next):
        if request.url.path in {"/metrics", "/health"}:
            return await call_next(request)
        start = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            labels = (request.method, route, str(status))
            REQUESTS.labels(*labels).inc()
            LATENCY.labels(*labels).observe(perf_counter() - start)

    return provider

