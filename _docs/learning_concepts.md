# Learning Concepts

A reference guide to the technologies, patterns, and concepts used in this project. Designed for anyone reviewing the codebase who wants to understand *why* a tool or pattern was chosen, not just *what* it does.

## Table of Contents

- [Architecture Patterns](#architecture-patterns)
  - [Calculate First, Reason Second](#calculate-first-reason-second)
  - [Factory Pattern](#factory-pattern)
  - [Microservices Separation](#microservices-separation)
- [Web Framework & API](#web-framework--api)
  - [FastAPI](#fastapi)
  - [Pydantic](#pydantic)
  - [Starlette Config](#starlette-config)
- [Data Processing](#data-processing)
  - [Pandas](#pandas)
  - [Supply Chain Formulas](#supply-chain-formulas)
- [LLM Integration](#llm-integration)
  - [Anthropic SDK](#anthropic-sdk)
  - [OpenAI SDK (Local Dev)](#openai-sdk-local-dev)
  - [Prompt Engineering](#prompt-engineering)
  - [Tenacity (Exponential Backoff)](#tenacity-exponential-backoff)
- [Testing & Evaluation](#testing--evaluation)
  - [pytest](#pytest)
  - [deepeval](#deepeval)
  - [GEval vs FaithfulnessMetric](#geval-vs-faithfulnessmetric)
  - [LLM-as-Judge Pattern](#llm-as-judge-pattern)
  - [Ground Truth Validation](#ground-truth-validation)
- [Observability](#observability)
  - [Structlog](#structlog)
  - [OpenTelemetry (OTEL)](#opentelemetry-otel)
  - [Langfuse](#langfuse)
  - [HyperDX](#hyperdx)
- [Frontend](#frontend)
  - [Streamlit](#streamlit)
- [Code Quality](#code-quality)
  - [Black](#black)
  - [Ruff](#ruff)
  - [mypy](#mypy)
  - [Pre-commit Hooks](#pre-commit-hooks)
- [DevOps & Deployment](#devops--deployment)
  - [uv](#uv)
  - [Docker & Docker Compose](#docker--docker-compose)
  - [GitHub Actions CI](#github-actions-ci)
  - [Fly.io](#flyio)

---

## Architecture Patterns

### Calculate First, Reason Second

**Where:** `sop_engine.py` (math) -> `llm_service.py` (narrative)

The single most important architectural decision in this project. LLMs are unreliable at arithmetic -- they hallucinate numbers, miscalculate percentages, and produce plausible-but-wrong financial figures. This architecture solves that by splitting the work:

1. **Pandas calculates everything deterministically** -- totals, growth rates, stock cover, reorder quantities
2. **The LLM receives a pre-computed JSON payload** and writes narrative reasoning around verified numbers

The LLM never sees raw CSV data. Every number in the briefing traces back to a Pandas calculation. This is validated by the [FaithfulnessMetric](#geval-vs-faithfulnessmetric) deepeval test.

See: [`_docs/adr/0001-calculate-first-reason-second.md`](adr/0001-calculate-first-reason-second.md)

### Factory Pattern

**Where:** `llm_service.py`

Instead of using a heavyweight abstraction layer like LiteLLM to switch between LLM providers, the code uses a simple factory pattern:

```python
if config.ENV == "production":
    return _call_anthropic(user_prompt)   # Anthropic SDK -> Claude
return _call_local(user_prompt)           # OpenAI SDK -> LM Studio
```

This keeps the codebase simple while supporting two completely different LLM backends (Anthropic's native SDK vs OpenAI-compatible local inference) without any shared abstraction. The caller (`generate_briefing`) doesn't know or care which backend is active.

### Microservices Separation

**Where:** `backend/` and `frontend/` directories

The backend (FastAPI) and frontend (Streamlit) are fully independent services, each with their own `pyproject.toml`, `uv.lock`, `Dockerfile`, and test suite. They communicate only via HTTP (`GET /api/v1/generate-sop`). This means:

- Either can be deployed, scaled, or replaced independently
- The frontend contains zero business logic -- it's a pure presentation layer
- Tests run in isolation for each service

---

## Web Framework & API

### FastAPI

**Where:** `api.py`

A modern Python web framework built on Starlette and Pydantic. Chosen for:

- **Automatic OpenAPI docs** -- the hiring manager can test endpoints at `/docs` without any setup
- **Pydantic integration** -- request/response models are type-checked at runtime
- **Async support** -- the lifespan context manager handles startup/shutdown cleanly

Key patterns used:
- `APIRouter` with `/api/v1` prefix for versioned endpoints
- `StreamingResponse` with `io.BytesIO` for CSV downloads without disk writes
- `HTTPException` for structured error responses with descriptive messages

### Pydantic

**Where:** `schemas.py`

Data validation library that enforces type contracts at runtime. Used in two distinct ways:

1. **Input validation** -- `SalesRow` validates every row of the CSV after Pandas loads it, catching missing columns or wrong types before any calculations run
2. **Response schemas** -- `SOPResponse`, `SOPMetrics`, `RedFlagItem` define the exact shape of the API response, giving consumers (the Streamlit frontend) a reliable contract

Pydantic v2 uses Rust-based validation under the hood, making it fast enough for row-by-row CSV validation.

### Starlette Config

**Where:** `config.py`

A lightweight alternative to python-dotenv for environment variable management. Reads from a `.env` file and provides typed access with defaults:

```python
config = Config(".env")
ENV: str = config("ENV", default="local")
```

Chosen over python-dotenv because Starlette is already a FastAPI dependency (zero extra cost) and provides cleaner typed access.

---

## Data Processing

### Pandas

**Where:** `sop_engine.py`

The Python data analysis library. All supply chain math is implemented as vectorized DataFrame operations -- no Python loops for calculations. Key techniques:

- **Boolean masking** -- `df[df["Is_At_Risk"]]` to filter at-risk SKUs
- **Safe division** -- `.replace(0, pd.NA)` before dividing to avoid ZeroDivisionError, then `.fillna(999)` for stagnant products
- **Conditional assignment** -- `.where(~is_bioactive, ...)` for the BioSynergy exception
- **Chained operations** -- `.clip(lower=0).astype(int)` to ensure non-negative integer reorder quantities

### Supply Chain Formulas

**Where:** `sop_engine.py`

Standard S&OP calculations implemented in Pandas:

| Formula | Purpose |
|---------|---------|
| `MoM_Growth_Avg` | Average of 3 month-over-month growth rates -- trend indicator |
| `Projected_M5_Sales` | Next month forecast: `M4 * (1 + MoM_Growth_Avg)` |
| `Current_Months_Cover` | How long current stock lasts: `Stock / Projected_Sales` |
| `Effective_Months_Cover` | Including pipeline: `(Stock + On_Order) / Projected_Sales` |
| `Total_Pipeline_Needed` | `(Target_Cover + Lead_Time) * Projected_Sales` |
| `Suggested_Reorder_Qty` | `MAX(0, Pipeline_Needed - Stock - On_Order)` |

The **BioSynergy exception** suppresses growth projection for new Q1 2026 products, using M4 actuals as a flat baseline to avoid over-ordering based on launch spikes.

---

## LLM Integration

### Anthropic SDK

**Where:** `llm_service.py` (`_call_anthropic`)

The native Anthropic Python SDK for calling Claude in production. Used instead of the OpenAI SDK because:

- Native support for Anthropic-specific features (system prompts as a first-class parameter)
- Direct access to `response.usage.input_tokens` / `output_tokens` for cost tracking
- No compatibility layer overhead

### OpenAI SDK (Local Dev)

**Where:** `llm_service.py` (`_call_local`)

The OpenAI Python SDK configured with `base_url=http://localhost:1234/v1` to point at LM Studio (or any OpenAI-compatible local server like vLLM, Ollama, etc.). This enables free, offline development without API calls.

### Prompt Engineering

**Where:** `templates/system_prompt.j2`, `templates/user_prompt.j2`, `prompts.py`

The prompts are carefully structured to produce consistent, evaluable output:

- **System prompt** -- sets the persona ("expert Supply Chain & S&OP Director") and constraints (5-minute read, Markdown, data-driven tone)
- **User prompt** -- 6 numbered instructions that map to specific briefing sections, each with clear deliverables
- **Delimiter instruction** -- `"End this section with exactly this line: **AIR FREIGHT SKU: <name>**"` enables deterministic extraction for evaluation

The delimiter pattern is a common technique for making LLM outputs machine-parseable without sacrificing readability.

### Tenacity (Exponential Backoff)

**Where:** `llm_service.py` (`_call_llm_with_retry`)

A Python retry library that wraps the LLM call with:

```python
@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(3), reraise=True)
```

This means: retry up to 3 times, waiting 2s, then 4s, then 8s (capped at 30s). Essential for production LLM calls which can fail due to rate limits, transient network errors, or API timeouts. `reraise=True` ensures the original exception propagates after exhausting retries.

---

## Testing & Evaluation

### pytest

**Where:** `backend/tests/`, `frontend/tests/`

The standard Python testing framework. Key features used:

- **Markers** -- `@pytest.mark.integration` to tag live LLM tests, excluded from CI via `addopts = "-m 'not integration'"`
- **`skipif`** -- conditional test skipping when no ground truth data exists
- **`conftest.py`** -- project-root `sys.path` configuration so imports resolve cleanly
- **Coverage** -- `pytest-cov` with `--cov-fail-under=70` minimum threshold

### deepeval

**Where:** `backend/tests/test_evals.py`

An open-source LLM evaluation framework. Unlike traditional unit tests that check exact values, deepeval uses an LLM judge to score outputs on qualitative criteria. This is critical for evaluating LLM outputs where:

- There isn't a single "correct" answer (the briefing can be written many ways)
- Quality is multi-dimensional (correctness, completeness, faithfulness)
- The evaluation itself requires reasoning (is this recommendation justified?)

### GEval vs FaithfulnessMetric

These are two different deepeval metrics that test fundamentally different things:

**GEval (G-Evaluation)**

A flexible, criteria-based metric where you define *what* to evaluate in natural language. The judge LLM generates evaluation steps from your criteria, then scores the output.

Used for:
- **Air Freight Correctness** -- "Is the recommended SKU among the highest-revenue at-risk SKUs, and is the recommendation logically justified?"
- **Briefing Completeness** -- "Does the briefing contain all 6 required sections?"

GEval is like giving a rubric to a human grader. You define the criteria, and the judge applies them. Good for subjective or multi-factor evaluations.

**FaithfulnessMetric**

A structured metric that extracts factual claims from the LLM output and checks each one against the source data. It answers: "Did the LLM make up any numbers?"

The process:
1. Judge extracts all claims from the briefing (e.g., "MGO 514+ generated $31,356 in revenue")
2. Judge extracts all facts from the data payload
3. Judge checks each claim against the facts
4. Score = claims supported / total claims

This is the most important metric for this project because it directly validates the "calculate first, reason second" architecture -- proving the LLM reasons faithfully over pre-computed data rather than hallucinating.

**When to use which:**

| Use GEval when... | Use FaithfulnessMetric when... |
|---|---|
| Evaluating subjective quality | Checking factual accuracy |
| Custom criteria that vary by use case | Verifying output against source data |
| No single right answer | Numbers and claims must be traceable |
| Scoring structure, tone, completeness | Detecting hallucination |

### LLM-as-Judge Pattern

**Where:** `test_evals.py` (`_get_opus_judge`)

The practice of using a stronger LLM to evaluate a weaker LLM's output. In this project:

- **Generator:** Claude Sonnet (produces the briefing)
- **Judge:** Claude Opus (evaluates the briefing via deepeval)

Using a stronger model as judge is important because the judge needs to reason about whether the generator's output is correct, complete, and faithful -- tasks that require equal or greater capability than generating the output in the first place. Using the same model to judge itself would be circular.

### Ground Truth Validation

**Where:** `test_evals.py`, `sop_engine.py` (`get_air_freight_candidate`)

The Air Freight Candidate is calculated deterministically by Pandas (`MAX(Revenue_M4)` among at-risk SKUs) but is **intentionally withheld** from the LLM's JSON payload. The LLM must independently reason about which SKU to recommend for air freight.

This creates a testable claim: if the LLM arrives at the same answer as the math, it demonstrates genuine reasoning over data rather than simply echoing a pre-supplied answer.

The test accepts the top 2 at-risk SKUs by revenue as valid answers, acknowledging that genuine reasoning may weigh multiple factors (revenue, cover ratio, premium positioning) differently.

---

## Observability

### Structlog

**Where:** `telemetry.py`

A structured logging library that outputs JSON instead of plain text. Every log line is a JSON object with consistent keys:

```json
{"event": "llm_call_complete", "env": "production", "input_tokens": 4722, "latency_seconds": 73.91}
```

This makes logs machine-parseable, searchable, and filterable in log aggregation tools. Traditional `print()` or `logging.info("message")` produces unstructured text that's hard to query at scale.

### OpenTelemetry (OTEL)

**Where:** `telemetry.py`

The industry-standard framework for distributed tracing. Traces follow a request through the system:

```
HTTP Request -> sop_engine.calculate -> llm.generate_briefing -> Response
```

Each step is a "span" with timing data. When exported via OTLP to a backend like HyperDX, this gives visibility into where time is spent (e.g., "the LLM call took 74 seconds out of a 75-second request").

Key concepts:
- **Tracer** -- creates spans for operations
- **SpanExporter** -- sends spans to an external system (OTLP/HTTP)
- **BatchSpanProcessor** -- buffers spans and exports in batches for efficiency

### Langfuse

**Where:** `telemetry.py`, `llm_service.py`

An LLM-specific observability platform. While OpenTelemetry tracks general request traces, Langfuse tracks LLM-specific data:

- The exact prompt sent to the model
- The model's full response
- Token counts (input/output) for cost tracking
- Latency per LLM call
- Model name and parameters

This is essential for debugging LLM behavior ("what prompt produced this bad output?") and monitoring costs.

### HyperDX

**Where:** `telemetry.py` (OTLP export target)

A full-stack observability platform that receives OpenTelemetry traces and structured logs via OTLP. Provides dashboards, alerting, and log search across all services. The OTLP exporter in `telemetry.py` sends data to HyperDX's ingestion endpoint.

---

## Frontend

### Streamlit

**Where:** `frontend/app.py`

A Python framework for building data dashboards with minimal code. Chosen because:

- **Zero JavaScript** -- the entire frontend is Python
- **Auto-reload** -- changes to `app.py` refresh the browser immediately
- **Built-in widgets** -- `st.metric()`, `st.dataframe()`, `st.download_button()` cover all the UI needs
- **`@st.cache_data`** -- caches the API response so the backend is only called once per session (not on every Streamlit re-render)

The frontend is a pure presentation layer with no business logic. It calls the backend API, renders the response, and provides a download button for the PO CSV.

---

## Code Quality

### Black

An opinionated Python code formatter. "Opinionated" means there are almost no configuration options -- it enforces one style everywhere. This eliminates style debates in code review and ensures consistent formatting across the codebase.

### Ruff

A fast Python linter written in Rust. Replaces Flake8, isort, and several other tools in a single binary. Catches code quality issues like unused imports, undefined names, and style violations. Runs in milliseconds even on large codebases.

### mypy

A static type checker for Python. Validates that type annotations (e.g., `def calculate(df: pd.DataFrame) -> pd.DataFrame`) are consistent throughout the code. Catches bugs like passing a string where an int is expected -- before the code runs.

### Pre-commit Hooks

**Where:** `.pre-commit-config.yaml`

A framework that runs checks automatically before each `git commit`. In this project, every commit must pass:

1. **Black** -- is the code formatted?
2. **Ruff** -- are there linting issues?
3. **mypy** -- do the types check?

If any check fails, the commit is blocked. This prevents broken or unformatted code from entering the repository.

---

## DevOps & Deployment

### uv

**Where:** `pyproject.toml`, `uv.lock`

A modern Python package manager written in Rust (by the creators of Ruff). Replaces pip, pip-tools, and virtualenv. Key advantages:

- **10-100x faster** than pip for dependency resolution and installation
- **Lockfile** (`uv.lock`) ensures reproducible builds
- **`uv run`** -- executes commands in the project's virtual environment without manual activation
- **Dependency groups** -- `[dependency-groups] dev` separates test/lint tools from production dependencies

### Docker & Docker Compose

**Where:** `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`

Containerization packages each service with its dependencies into a portable image. Docker Compose orchestrates multiple containers:

- `backend` (FastAPI on port 8000)
- `frontend` (Streamlit on port 8501)
- Service discovery via container names and `host.docker.internal` for host services

The `make reqs` step exports `uv.lock` to `requirements.txt` for Docker builds (since Docker images don't use uv).

### GitHub Actions CI

**Where:** `.github/workflows/ci.yml`

Automated testing pipeline that runs on every push to `main` and every pull request:

1. Sets up Python 3.12 via uv
2. Installs dependencies (`uv sync`)
3. Runs pre-commit hooks (Black, Ruff, mypy)
4. Runs pytest with coverage for both backend and frontend
5. Uploads coverage reports to Codecov

Integration tests (live LLM) are automatically skipped in CI via the `addopts = "-m 'not integration'"` pytest config.

### Fly.io

**Where:** `backend/fly.toml`, `frontend/fly.toml`

A platform for deploying containerized applications globally. Each microservice is deployed as a separate Fly app:

1. `fly launch` creates the app and provisions infrastructure
2. `fly secrets set` configures environment variables (API keys, service URLs)
3. `fly deploy` builds and deploys the Docker image

The frontend's `BACKEND_URL` points to the backend's Fly.io URL for cross-service communication in production.
