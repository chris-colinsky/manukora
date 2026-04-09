#!/usr/bin/env python3
"""Standalone test: send one trace and one log to HyperDX cloud.

Usage:
    OTEL_EXPORTER_OTLP_ENDPOINT=https://in-otel.hyperdx.io \
    OTEL_EXPORTER_OTLP_HEADERS="authorization=<YOUR_KEY>" \
    uv run python scripts/test_hyperdx.py
"""

import logging
import os
import time

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
raw_headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")

if not endpoint:
    print("ERROR: Set OTEL_EXPORTER_OTLP_ENDPOINT")
    exit(1)

print(f"Endpoint: {endpoint}")
print(f"Headers raw: {repr(raw_headers)}")

# Parse headers
headers = {}
if raw_headers:
    for pair in raw_headers.split(","):
        if "=" in pair:
            k, _, v = pair.strip().partition("=")
            headers[k.strip()] = v.strip()

print(f"Parsed headers: { {k: v[:10]+'...' for k,v in headers.items()} }")

resource = Resource.create({"service.name": "hyperdx-test"})

# --- Approach 1: Explicit endpoints ---
print("\n--- Approach 1: Explicit /v1/traces and /v1/logs ---")
try:
    tp1 = TracerProvider(resource=resource)
    tp1.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)
        )
    )
    tracer1 = tp1.get_tracer("test")
    with tracer1.start_as_current_span("test-explicit") as span:
        span.set_attribute("test.approach", "explicit")
        time.sleep(0.1)
    tp1.force_flush()
    tp1.shutdown()
    print("  Traces: sent (no error)")
except Exception as e:
    print(f"  Traces ERROR: {e}")

try:
    lp1 = LoggerProvider(resource=resource)
    lp1.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers)
        )
    )
    handler1 = LoggingHandler(level=logging.DEBUG, logger_provider=lp1)
    logger1 = logging.getLogger("test.explicit")
    logger1.addHandler(handler1)
    logger1.setLevel(logging.INFO)
    logger1.info("Test log message - explicit endpoints")
    lp1.force_flush()
    lp1.shutdown()
    print("  Logs: sent (no error)")
except Exception as e:
    print(f"  Logs ERROR: {e}")

# --- Approach 2: Base URL only (SDK appends paths) ---
print("\n--- Approach 2: Base URL only (SDK auto-appends) ---")
try:
    tp2 = TracerProvider(resource=resource)
    tp2.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    tracer2 = tp2.get_tracer("test")
    with tracer2.start_as_current_span("test-base-url") as span:
        span.set_attribute("test.approach", "base-url")
        time.sleep(0.1)
    tp2.force_flush()
    tp2.shutdown()
    print("  Traces: sent (no error)")
except Exception as e:
    print(f"  Traces ERROR: {e}")

try:
    lp2 = LoggerProvider(resource=resource)
    lp2.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, headers=headers))
    )
    handler2 = LoggingHandler(level=logging.DEBUG, logger_provider=lp2)
    logger2 = logging.getLogger("test.baseurl")
    logger2.addHandler(handler2)
    logger2.setLevel(logging.INFO)
    logger2.info("Test log message - base URL")
    lp2.force_flush()
    lp2.shutdown()
    print("  Logs: sent (no error)")
except Exception as e:
    print(f"  Logs ERROR: {e}")

# --- Approach 3: Let SDK read env vars natively ---
print("\n--- Approach 3: No args (SDK reads env vars) ---")
try:
    tp3 = TracerProvider(resource=resource)
    tp3.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    tracer3 = tp3.get_tracer("test")
    with tracer3.start_as_current_span("test-env-vars") as span:
        span.set_attribute("test.approach", "env-vars")
        time.sleep(0.1)
    tp3.force_flush()
    tp3.shutdown()
    print("  Traces: sent (no error)")
except Exception as e:
    print(f"  Traces ERROR: {e}")

try:
    lp3 = LoggerProvider(resource=resource)
    lp3.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    handler3 = LoggingHandler(level=logging.DEBUG, logger_provider=lp3)
    logger3 = logging.getLogger("test.envvars")
    logger3.addHandler(handler3)
    logger3.setLevel(logging.INFO)
    logger3.info("Test log message - env vars")
    lp3.force_flush()
    lp3.shutdown()
    print("  Logs: sent (no error)")
except Exception as e:
    print(f"  Logs ERROR: {e}")

print("\nDone. Check HyperDX for service 'hyperdx-test' in the next 30 seconds.")
