"""Observability setup: Structlog JSON logging, OpenTelemetry traces, and Langfuse.

OTEL and Langfuse are kept intentionally separate:
- OTEL traces go to HyperDX for infrastructure observability
- Langfuse traces LLM calls (prompts, outputs, tokens, latency)

We intentionally do NOT call trace.set_tracer_provider() globally.
Setting a global provider causes Langfuse to emit its LLM spans through
OTEL, duplicating data already in the Langfuse UI.  Instead, we keep an
explicit _tracer_provider and use it via get_tracer().
"""

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
_tracer_provider: TracerProvider | None = None


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
    """Initialise OpenTelemetry tracing with an explicit (non-global) provider.

    If OTEL_EXPORTER_OTLP_ENDPOINT is configured, exports traces via OTLP/HTTP
    (e.g., to HyperDX).  Otherwise, installs a no-op in-memory exporter so the
    application runs without external dependencies.

    NOTE: We intentionally do NOT call trace.set_tracer_provider() here.
    Setting a global provider causes Langfuse v3+ to emit its LLM/graph
    spans through it, duplicating data already in the Langfuse UI.
    Instead, get_tracer() uses _tracer_provider explicitly.
    """
    global _test_exporter, _tracer_provider

    resource = Resource.create({"service.name": config.OTEL_SERVICE_NAME})
    _tracer_provider = TracerProvider(resource=resource)

    if config.OTEL_EXPORTER_OTLP_ENDPOINT:
        headers: dict[str, str] = {}
        if config.HYPERDX_API_KEY:
            headers["authorization"] = config.HYPERDX_API_KEY

        exporter = OTLPSpanExporter(
            endpoint=f"{config.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces",
            headers=headers,
        )
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        _test_exporter = InMemorySpanExporter()
        _tracer_provider.add_span_processor(SimpleSpanProcessor(_test_exporter))


def get_tracer() -> trace.Tracer:
    """Return the application-wide OpenTelemetry tracer.

    Uses the explicit (non-global) provider to avoid interfering with
    Langfuse's own tracing.

    Returns:
        A Tracer instance scoped to the honey-backend service.
    """
    if _tracer_provider is not None:
        return _tracer_provider.get_tracer(config.OTEL_SERVICE_NAME)
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


def shutdown() -> None:
    """Flush and shut down all observability components."""
    if _langfuse_client is not None:
        _langfuse_client.flush()
    if _tracer_provider is not None:
        _tracer_provider.shutdown()


def setup() -> None:
    """Initialise all observability components.  Call once at application startup."""
    setup_logging()
    setup_tracing()
