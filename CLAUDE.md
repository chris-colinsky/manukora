# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A job submission for Manukora (NZ DTC honey brand). The task: build an AI agent that generates a weekly S&OP (Sales & Operations Planning) briefing for non-technical executives. The system must do real supply chain math in Python/Pandas, then pass the results to an LLM for narrative reasoning — not raw CSV-to-LLM.

The requirements are fully specified in `_reqs/submission-strategy-part-1.md`. Read that file before implementing anything.

## Package Management

Use `uv` exclusively — not `pip`. Add dependencies with `uv add`, never manually edit pyproject.toml dependencies.

```bash
uv sync              # install all deps
uv add <package>     # add a dependency
uv run pytest        # run tests via uv
```

## Commands (once implemented)

```bash
make test            # pytest with coverage across backend + frontend
make lint            # black + ruff + mypy
make pre-commit      # install/run pre-commit hooks
make reqs            # uv export -> requirements.txt (required before Docker build)
make docker-build    # depends on make reqs
make up              # docker-compose up
```

## Architecture

**Critical rule: Calculate first, reason second.** Never feed raw CSV to the LLM.

```
sales-data.csv
    → backend/sop_engine.py    (Pandas: all supply chain math)
    → backend/schemas.py       (Pydantic validation)
    → backend/llm_service.py   (Factory: local OpenAI-compat OR prod Anthropic)
    → backend/api.py           (FastAPI: GET /api/v1/generate-sop, GET /api/v1/download-pos)
    → frontend/app.py          (Streamlit: auto-loads on open, no user clicks required)
```

**Backend** (`backend/`) and **Frontend** (`frontend/`) are separate microservices, each with their own `pyproject.toml`, `uv.lock`, and `Dockerfile`.

## Key Implementation Details

### sop_engine.py (Pandas calculations)
The engine must implement these in order:
1. **Omni-channel totals**: `Total_M[1-4] = Shopify + Amazon`, `Revenue_M4 = Total_M4 * Retail_Price_USD`
2. **Growth & projection**: `MoM_Growth_Avg` = average of 3 MoM rates; `Projected_M5 = Total_M4 * (1 + MoM_Growth_Avg)`. **Exception**: SKUs containing "Bioactive Blend" use `Projected_M5 = Total_M4` (new Q1 2026 products, suppress launch spike).
3. **Stock cover**: `Current_Months_Cover = Stock_On_Hand / Projected_M5`; `Effective_Months_Cover = (Stock_On_Hand + Units_On_Order) / Projected_M5`; `Is_At_Risk = Effective_Months_Cover < Target_Months_Cover`. If `Projected_M5 == 0`, set both cover values to 999.
4. **Reorder qty**: `Total_Pipeline_Needed = (Target_Months_Cover + Order_Arrival_Months) * Projected_M5`; `Suggested_Reorder_Qty = MAX(0, Total_Pipeline_Needed - Stock_On_Hand - Units_On_Order)`
5. **Air Freight Candidate**: `SKU with MAX(Revenue_M4)` among `Is_At_Risk == True`. **Do NOT pass this to the LLM** — it's ground truth for deepeval tests only.

### llm_service.py (Factory pattern)
- `ENV=local` → OpenAI SDK with `base_url=http://localhost:1234/v1` (LM Studio)
- `ENV=production` → Anthropic SDK, `claude-3-5-sonnet` model
- Wrap LLM call with `tenacity` exponential backoff

### LLM Evaluation (deepeval)
The key test in `backend/tests/test_evals.py`: parse the LLM's markdown output to extract which SKU it recommends for air freight, then assert it matches the Pandas-calculated `Air_Freight_Candidate`. This validates genuine LLM reasoning.

### Config
Use `starlette.config.Config` in `backend/config.py`. Required env vars:
- `ENV` (local/production)
- `DATA_FILE_PATH` (default: `data/sales-data.csv`)
- `ANTHROPIC_API_KEY` (prod only)
- `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` (HyperDX)
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`

### Observability
- Structlog for structured JSON logs
- OpenTelemetry → HyperDX (OTLP export)
- Langfuse wrapping every LLM call (traces prompt, output, latency, tokens)

## Quality Gates
- Pre-commit: `black` (format) + `ruff` (lint) + `mypy` (types)
- CI: GitHub Actions on all PRs and pushes to main
- Coverage: minimum 70% for both backend and frontend
- All functions need Google-style docstrings and type hints (mypy enforced)

## Input Data

`sales-data.csv` (also bundled at `backend/data/sales-data.csv`) — 12 SKUs:
- Manuka Honey at MGO 100+, 263+, 514+, 850+, 1700+ in 250g/500g/100g
- Propolis Tincture 30ml
- Bioactive Blend Immunity/Energy/Recovery 250g (new Q1 2026 — special forecasting logic applies)

## Deployment (Fly.io)
Two separate Fly apps: `cd backend && fly launch`, then `cd frontend && fly launch` with `BACKEND_URL` pointing to the backend app.
