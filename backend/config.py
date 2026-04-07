"""Centralised environment variable management using Starlette Config."""

from starlette.config import Config

config = Config(".env")

ENV: str = config("ENV", default="local")
DATA_FILE_PATH: str = config("DATA_FILE_PATH", default="data/sales-data.csv")

ANTHROPIC_API_KEY: str = config("ANTHROPIC_API_KEY", default="")

# Local LLM (LM Studio / vLLM)
LOCAL_LLM_BASE_URL: str = config("LOCAL_LLM_BASE_URL", default="http://localhost:1234/v1")
LOCAL_LLM_MODEL: str = config("LOCAL_LLM_MODEL", default="local-model")

# OpenTelemetry / HyperDX
HYPERDX_API_KEY: str = config("HYPERDX_API_KEY", default="")
OTEL_EXPORTER_OTLP_ENDPOINT: str = config("OTEL_EXPORTER_OTLP_ENDPOINT", default="")
OTEL_SERVICE_NAME: str = config("OTEL_SERVICE_NAME", default="honey-backend")

# Langfuse
LANGFUSE_PUBLIC_KEY: str = config("LANGFUSE_PUBLIC_KEY", default="")
LANGFUSE_SECRET_KEY: str = config("LANGFUSE_SECRET_KEY", default="")
LANGFUSE_HOST: str = config("LANGFUSE_HOST", default="http://localhost:3000")
