"""Observability setup: Structlog JSON logging, OpenTelemetry traces, and Langfuse."""

import logging

import structlog
from langfuse import Langfuse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import config

_langfuse_client: Langfuse | None = None
_test_exporter: InMemorySpanExporter | None = None


def setup_logging() -> None:
    """Configure Structlog to emit structured JSON logs to stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def setup_tracing() -> None:
    """Initialise OpenTelemetry tracing.

    If OTEL_EXPORTER_OTLP_ENDPOINT is configured, exports traces via OTLP/HTTP
    (e.g., to HyperDX).  Otherwise, installs a no-op in-memory exporter so the
    application runs without external dependencies.
    """
    global _test_exporter

    resource = Resource.create({"service.name": config.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    if config.OTEL_EXPORTER_OTLP_ENDPOINT:
        # Pass HYPERDX_API_KEY as Authorization header (same as configure_opentelemetry()).
        # For local self-hosted HyperDX with no auth, leave HYPERDX_API_KEY unset.
        headers: dict[str, str] = {}
        if config.HYPERDX_API_KEY:
            headers["authorization"] = config.HYPERDX_API_KEY

        exporter = OTLPSpanExporter(
            endpoint=f"{config.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces",
            headers=headers,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        # No-op: keep spans in memory so tests can inspect them without a live backend.
        _test_exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_test_exporter))

    trace.set_tracer_provider(provider)


def get_tracer() -> trace.Tracer:
    """Return the application-wide OpenTelemetry tracer.

    Returns:
        A Tracer instance scoped to the honey-backend service.
    """
    return trace.get_tracer(config.OTEL_SERVICE_NAME)


def get_langfuse() -> Langfuse | None:
    """Return a Langfuse client if credentials are configured, else None.

    Returns:
        A configured Langfuse instance, or None when keys are absent.
    """
    global _langfuse_client

    if _langfuse_client is not None:
        return _langfuse_client

    if config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY:
        _langfuse_client = Langfuse(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            secret_key=config.LANGFUSE_SECRET_KEY,
            host=config.LANGFUSE_HOST,
        )

    return _langfuse_client


def setup() -> None:
    """Initialise all observability components.  Call once at application startup."""
    setup_logging()
    setup_tracing()
