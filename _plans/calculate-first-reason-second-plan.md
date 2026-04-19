# Plan: Calculate First, Reason Second — Reference Architecture

## Summary

Build a production-grade, microservices-based S&OP briefing system demonstrating the "Calculate First, Reason Second" pattern. A FastAPI backend reads `sales-data.csv`, runs all supply chain math in Pandas (Calculate First), then passes a clean JSON payload to the Anthropic Claude API for narrative reasoning (Reason Second). A Streamlit frontend auto-loads on open and presents the LLM briefing, KPI widgets, and a downloadable draft PO CSV. The system includes full observability (local Langfuse + OpenTelemetry), LLM evaluation via deepeval, pre-commit hooks, GitHub Actions CI, Docker containerization, and Fly.io deployment.

## Requirements Reference

`_reqs/calculate-first-reason-second.md`

## Clarifications & Decisions

- **deepeval Air Freight extraction** → LLM prompt will require output of a clearly delimited line `**AIR FREIGHT SKU: <name>**` inside a `## Strategic Priority: Air Freight Recommendation` section; regex parses this deterministically.
- **Fly.io** → Full cloud deployment required for live demo.
- **Observability** → Locally self-hosted Langfuse via Docker in `docker-compose.yml`; OTLP exporter configured but no-op when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset (stretch: wire to real HyperDX).
- **Claude model** → `claude-sonnet-4-6` (latest available Sonnet).
- **download-pos endpoint** → Returns all SKUs where `Suggested_Reorder_Qty > 0` as a downloadable CSV.
- **BACKEND_URL default** → `http://localhost:8000` for local dev outside Docker.

---

## Implementation Plan

### Phase 1: Project Scaffolding

- [ ] Initialize `backend/` as a standalone uv project (`uv init backend`); add all required backend dependencies
- [ ] Initialize `frontend/` as a standalone uv project (`uv init frontend`); add all required frontend dependencies
- [ ] Copy `sales-data.csv` → `backend/data/sales-data.csv`
- [ ] Create root `Makefile` with targets: `test`, `lint`, `pre-commit`, `reqs`, `docker-build`, `up`
- [ ] Create `.pre-commit-config.yaml` with `black`, `ruff`, `mypy` hooks
- [ ] Create `.github/workflows/ci.yml` (trigger on PR + push to main; uv setup, pre-commit, pytest with coverage for both services)
- [ ] Create `_docs/adr/0001-calculate-first-reason-second.md`
- [ ] Create `_docs/architecture.mmd` (Mermaid diagram of full data flow)

### Phase 2: Backend — Config, Schemas & Calculations

- [ ] `backend/config.py` — Starlette `Config` loading `ENV`, `DATA_FILE_PATH` (default: `data/sales-data.csv`), `ANTHROPIC_API_KEY`, `OTEL_*`, `LANGFUSE_*` vars
- [ ] `backend/schemas.py` — Pydantic models: `SalesRow` (validates each CSV row), `SOPMetrics`, `RedFlagItem`, `SOPResponse` (the full API response shape)
- [ ] `backend/sop_engine.py` — Pure Pandas calculation engine:
  - Omni-channel totals (Total_M1–M4, Revenue_M4)
  - MoM_Growth_Avg; Projected_M5 (with BioSynergy exception → use M4 as baseline)
  - Current_Months_Cover, Effective_Months_Cover (division-by-zero → 999)
  - Is_At_Risk flag
  - Total_Pipeline_Needed, Suggested_Reorder_Qty
  - Air_Freight_Candidate (internal only — NOT in JSON payload to LLM)
  - Poor performer filter (MoM_Growth_Avg < 0 AND Effective_Months_Cover > 6)

### Phase 3: Backend — LLM Service & Telemetry

- [ ] `backend/telemetry.py` — Structlog JSON formatter; OpenTelemetry SDK + OTLP exporter (no-op if endpoint unset); Langfuse client initialisation
- [ ] `backend/llm_service.py` — Factory pattern:
  - `ENV=local` → OpenAI SDK with `base_url=http://localhost:1234/v1`
  - `ENV=production` → Anthropic SDK with `claude-sonnet-4-6`
  - Tenacity `@retry(wait=wait_exponential(...), stop=stop_after_attempt(3))` around the API call
  - Langfuse trace wrapping (prompt, output, latency, tokens)
  - System prompt and user prompt exactly as specified in reqs §8
  - User prompt instructs LLM to output Air Freight recommendation as: `**AIR FREIGHT SKU: <full SKU name>**`

### Phase 4: Backend — API

- [ ] `backend/api.py` — FastAPI app with:
  - `GET /api/v1/generate-sop` → reads CSV via config path, validates with Pydantic, runs sop_engine, calls llm_service, returns `SOPResponse` JSON
  - `GET /api/v1/download-pos` → runs sop_engine, filters `Suggested_Reorder_Qty > 0`, returns `StreamingResponse` with CSV (Content-Disposition: attachment)
  - OpenAPI/Swagger auto-configured (FastAPI default)
  - Structured error handling: missing columns / type errors → 500 with descriptive log

### Phase 5: Frontend

- [ ] `frontend/app.py` — Streamlit app:
  - `BACKEND_URL` from env (default: `http://localhost:8000`)
  - `@st.cache_data` wrapping the `GET /api/v1/generate-sop` call — fires once on load
  - `st.spinner("Analyzing omnichannel data and generating S&OP insights...")` while loading
  - `st.markdown()` for the LLM briefing
  - `st.metric()` widgets for `total_m4_revenue` and `skus_at_risk`
  - `st.dataframe()` for `red_flag_data`
  - `st.download_button("Download Draft POs (CSV)")` calling `/api/v1/download-pos`
  - Full error handling if API is unreachable

### Phase 6: Tests

- [ ] `backend/tests/test_sop_engine.py` — Unit tests for every formula in sop_engine:
  - Happy path for all calculations
  - BioSynergy projection exception
  - Division-by-zero edge case (Projected_M5 = 0 → cover = 999)
  - Is_At_Risk flag logic
  - Reorder qty formula
  - Poor performer filter
- [ ] `backend/tests/test_api.py` — FastAPI `TestClient` tests for both endpoints; mock sop_engine and llm_service
- [ ] `backend/tests/test_evals.py` — deepeval test:
  - Run real sop_engine on `sales-data.csv` to get `Air_Freight_Candidate` (ground truth)
  - Call the full generate-sop pipeline with a test LLM (or mock LLM response fixture)
  - Parse `**AIR FREIGHT SKU: <name>**` from the markdown with regex
  - Assert extracted SKU == `Air_Freight_Candidate`
- [ ] `frontend/tests/test_app.py` — Streamlit `AppTest` tests:
  - Mock API returning a valid `SOPResponse`; verify briefing and widgets render
  - Mock API returning an error; verify error state renders without crash
- [ ] Verify coverage ≥ 70% for both backend and frontend

### Phase 7: Containerization

- [ ] `backend/Dockerfile` — copies `data/`, installs from `requirements.txt`, runs `uvicorn api:app --host 0.0.0.0 --port 8000`
- [ ] `frontend/Dockerfile` — installs from `requirements.txt`, runs `streamlit run app.py --server.address 0.0.0.0 --server.port 8501`
- [ ] `docker-compose.yml` — orchestrates: `backend`, `frontend`, `langfuse` (self-hosted), `langfuse-db` (Postgres); wires env vars and network

### Phase 8: Documentation & README

- [ ] `README.md` — shields.io badges (CI, coverage, Python 3.12); TL;DR; embedded Mermaid diagram; copy-pasteable local dev + Docker commands; env vars table
- [ ] Ensure all functions have Google-style docstrings and type hints (mypy passes clean)

### Phase 9: Fly.io Deployment

- [ ] `backend/fly.toml` — app config for FastAPI service (port 8000)
- [ ] `frontend/fly.toml` — app config for Streamlit service (port 8501); `BACKEND_URL` secret set to backend Fly internal URL
- [ ] Document deployment steps in README

---

## Files to Create / Modify

```
backend/
  pyproject.toml            — uv project with all backend deps
  config.py                 — Starlette Config
  schemas.py                — Pydantic models
  sop_engine.py             — Pandas calculation engine
  llm_service.py            — LLM factory + tenacity + Langfuse
  telemetry.py              — Structlog + OTel + Langfuse init
  api.py                    — FastAPI routes
  Dockerfile
  fly.toml
  data/sales-data.csv       — copied from root
  tests/test_sop_engine.py
  tests/test_api.py
  tests/test_evals.py

frontend/
  pyproject.toml            — uv project with all frontend deps
  app.py                    — Streamlit UI
  Dockerfile
  fly.toml
  tests/test_app.py

_docs/
  adr/0001-calculate-first-reason-second.md
  architecture.mmd

_plans/
  calculate-first-reason-second-plan.md — this file

.github/workflows/ci.yml
.pre-commit-config.yaml
Makefile
docker-compose.yml
README.md                   — rewrite with badges, diagram, full setup guide
```

---

## Out of Scope

- Morning Intelligence Brief extension — separate deliverable
- Real cloud HyperDX account (OTLP exporter present but no-op without endpoint)
- Real cloud Langfuse (locally self-hosted in Docker only, unless stretch goal reached)
- Authentication / API key protection on FastAPI endpoints
- Historical data beyond the 4 months in `sales-data.csv`

## Open Questions

- Fly.io org/team to deploy under — user will supply `fly launch` context at deploy time.
- Stretch goal for real HyperDX: revisit after core submission is complete.
