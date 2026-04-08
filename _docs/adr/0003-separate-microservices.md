# ADR 0003: Separate Backend and Frontend Microservices

**Status:** Accepted  
**Date:** 2026-04-06

## Context

The system has two distinct runtime concerns:

- **Backend:** CPU-bound Pandas calculations, LLM API calls, and a REST API. Written in FastAPI.
- **Frontend:** A read-only executive dashboard that displays pre-computed results. Written in Streamlit.

We need to decide whether to serve these as a single application or as independent services.

## Decision

Backend and frontend are deployed as **separate microservices**, each with their own:

- `pyproject.toml` and `uv.lock` (independent dependency trees)
- `Dockerfile` (independent build and deployment)
- `.env` file (independent configuration)
- Fly.io app (independent scaling and deployment lifecycle)

The frontend communicates with the backend exclusively via HTTP (`BACKEND_URL`).

## Rationale

**Dependency isolation.** Streamlit, pandas, and FastAPI have overlapping but distinct dependency graphs. A single `pyproject.toml` risks version conflicts and unnecessarily inflates both container images.

**Independent scaling.** The backend is compute-heavy (Pandas + LLM calls). The frontend is almost entirely I/O-bound (one HTTP request per session). They have different scaling profiles.

**Independent deployment.** A prompt change in the backend does not require redeploying the frontend, and a UI change does not require restarting the API server.

**Streamlit is not suited to being embedded in FastAPI.** Serving Streamlit from within a FastAPI app requires non-standard process management. Running them as separate services follows both frameworks' documented deployment patterns.

## Consequences

- Local development requires two terminal sessions (or Docker Compose).
- The frontend has a hard runtime dependency on the backend being reachable at `BACKEND_URL`. A backend outage surfaces as a Streamlit error.
- Each service must independently manage its own `.env` — shared config (e.g., `OTEL_SERVICE_NAME`) must be set in both.
