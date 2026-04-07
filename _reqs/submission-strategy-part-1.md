# S&OP AI Automation - Requirements Document

## 1. Project Overview & Strategy

**Goal:** Automate a weekly Sales & Operations Planning (S&OP) briefing for a DTC honey brand (Shopify & Amazon). The output must be a 5-minute read for non-technical executives, identifying sales trends, stock risks, and making calculated reorder recommendations.

This is Part 1 of the [Submission Guidlines](../_docs/submission_guidelines.md)

**Architectural Strategy: Microservices ("Calculate First, Reason Second")**

Do not feed raw CSV data to an LLM. LLMs are prone to arithmetic hallucinations. The system must follow a modular, decoupled pipeline utilizing a REST API and a separate frontend:

1. **Data Processing Engine (Python/Pandas):** The backend reads a locally mounted CSV file, aggregates omnichannel sales, calculates historical run-rates, projects future demand, and calculates exact reorder quantities using standard supply chain formulas.  
2. **API & Reasoning Layer (FastAPI + LLM):** A FastAPI microservice that exposes a simple endpoint to trigger the process, runs the Pandas calculations, transforms the output into a JSON payload, and calls the LLM with a strict prompt to generate the narrative.  
3. **Presentation Layer (Streamlit):** A lightweight frontend that acts as the executive dashboard. It requests the data from the backend API and renders the returned JSON insights and Markdown briefing with zero configuration required by the user.

## 2. Data Schema Overview

The input is a CSV (sales-data.csv) with 12 rows (SKUs) and the following schema:

* SKU (String): Product identifier.  
* Shopify_Units_M1 to M4 (Int): Monthly unit sales on Shopify (M1 = oldest, M4 = most recent).  
* Amazon_Units_M1 to M4 (Int): Monthly unit sales on Amazon.  
* Stock_On_Hand (Int): Current warehouse inventory.  
* Units_On_Order (Int): Inventory currently in transit/manufacturing.  
* Order_Arrival_Months (Int): Lead time for new orders to arrive (in months).  
* Target_Months_Cover (Int): The ideal amount of stock to hold (in months of supply).  
* Retail_Price_USD (Float): Retail price per unit.

## 3. Business Logic & Calculations (Python Layer)

The backend engine must implement the following calculations for *each SKU* using Pandas. **Below are the formulas, what they represent, how they drive insights, and how the LLM should utilize them:**

### **A. Omni-Channel Totals**

- **Formulas:** 
  - Total_M[1-4] = Shopify_Units_M[1-4] + Amazon_Units_M[1-4]  
  - Revenue_M4 = Total_M4 * Retail_Price_USD  
- **What it represents:** The combined sales volume across all platforms and the gross revenue generated in the most recent month.  
- **Insights:** Identifies the true top-performing SKUs for the business by revenue and overall volume, removing channel-specific silos.  
- **LLM Instructions:** The LLM should use Revenue_M4 to identify and highlight the top 2-3 performing SKUs in the "Top Movers" section, discussing their total omnichannel volume.

### B. Growth & Projections (The Seasonality Caveat)

- **Formulas:**  
  - MoM_Growth_Avg = Average of ((M2-M1)/M1, (M3-M2)/M2, (M4-M3)/M3)  
  - Projected_M5_Sales = Total_M4 * (1 + MoM_Growth_Avg) *(Note: Ensure no negative projections).*  
  - **NEW PRODUCT EXCEPTION:** If SKU contains "Bioactive Blend", set Projected_M5_Sales = Total_M4. (Because these are new Q1 2026 releases, their initial growth trend is artificially high. We treat their most recent month as the steady-state baseline to avoid over-forecasting).  
- **What it represents:** The historical month-over-month growth rate and a realistic forecast of next month's sales based on that current momentum.  
- **Insights:** Shows whether a product's demand is accelerating or dying out. Using projected M5 sales prevents stockouts for rapidly growing products. **Crucial Limit:** This is a "naive" forecasting model based strictly on recent momentum. It *does not* account for seasonality (e.g., honey and immunity products naturally spiking in winter or dropping in summer).  
- **LLM Instructions:** The LLM should reference MoM_Growth_Avg to add narrative context (e.g., "MGO 263+ is up 10% MoM"). **Importantly, the LLM must explicitly caveat** that these projections and stock risks are based purely on 4-month trailing momentum and advise the executive team to overlay their knowledge of upcoming seasonal shifts when reviewing the reorder recommendations. It must also explicitly mention the conservative baseline approach taken for the new Bioactive Blend products.

### C. Stock Cover Status

- **Formulas:**  
  - Current_Months_Cover = Stock_On_Hand / Projected_M5_Sales  
  - Effective_Months_Cover = (Stock_On_Hand + Units_On_Order) / Projected_M5_Sales
  - *Edge Case (Division by Zero):* If Projected_M5_Sales is 0, set Current_Months_Cover and Effective_Months_Cover to a static high number (e.g., 999) to prevent ZeroDivisionError and flag the item as completely stagnant.  
  - Is_At_Risk = True if Effective_Months_Cover < Target_Months_Cover else False  
- **What it represents:** How long current warehouse inventory will last (Current), how long it will last *including* incoming shipments (Effective), and a boolean flag alerting if this total falls below the company's safety threshold.  
- **Insights:** Instantly flags vulnerabilities in the supply chain. If Is_At_Risk is True, it means the company will likely stock out or drop below safety levels before new stock can be arranged.  
- **LLM Instructions:** The LLM must filter the payload for SKUs where Is_At_Risk == True to populate the "Red Flags" section. It should explain the risk plainly (e.g., "We only have 1.3 months of effective cover left, compared to our target of 2.0 months").

### D. Advanced Reorder Formula (Factoring Lead Time)

- **Formulas:**  
  - *To prevent stockouts during lead time, we must order enough to cover the lead time PLUS the target safety stock.*  
  - Total_Pipeline_Needed = (Target_Months_Cover + Order_Arrival_Months) * Projected_M5_Sales  
  - Current_Pipeline = Stock_On_Hand + Units_On_Order  
  - Suggested_Reorder_Qty = MAX(0, Total_Pipeline_Needed - Current_Pipeline)  
- **What it represents:** The mathematically optimal number of units to order *today* to satisfy demand while waiting for the product to arrive, plus enough to maintain the target safety stock upon arrival.  
- **Insights:** Removes human guesswork from purchasing and protects cash flow by preventing over-ordering. Explicitly accounts for shipping/manufacturing lead times.  
- **LLM Instructions:** The LLM must use Suggested_Reorder_Qty as its definitive recommendation for 3 SKUs. It must translate the math into plain-English reasoning (e.g., "I recommend ordering 500 units. Because it takes 2 months for inventory to arrive, we need to order enough to cover that lead time plus our 2-month safety stock target").

### E. Poor Performers & Dead Stock

- **Formulas:**  
  * Filter for SKUs with MoM_Growth_Avg < 0 AND high Effective_Months_Cover (e.g., > 6 months).  
- **What it represents:** Capital tied up in slow-moving or declining inventory.  
- **Insights:** Identifies candidates for liquidation to improve cash flow and warehouse utilization.  
- **LLM Instructions:** The LLM must explicitly identify the worst-performing SKU (poor performers) and reason about whether to implement a discount or bundling strategy to free up working capital.

### F. Strategic Air Freight Calculation (For LLM Validation)

- **Formulas:**  
  - Filter for Is_At_Risk == True.  
  - Air_Freight_Candidate = SKU with MAX(Revenue_M4) among the filtered list.  
- **What it represents:** The single most critical SKU to the business's top line that is currently at risk of stocking out.  
- **Architecture Rule (CRITICAL):** Do **NOT** pass this calculated Air_Freight_Candidate to the LLM in the JSON payload. This is our "Ground Truth" value. We will ask the LLM to deduce this independently in the prompt and use this Pandas-calculated value in our deepeval test suite to validate the LLM's reasoning capabilities.

## 4. Technical Requirements & Modularity

* **Language:** Python 3.12  
* **Package Manager:** uv (Use uv init and uv add to manage dependencies in pyproject.toml)  
* **Libraries:** fastapi, uvicorn, starlette (for Config), pandas, anthropic, openai (for local dev only), streamlit, requests, pytest, pydantic, tenacity, structlog, opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp, langfuse, deepeval.  
* **Code Structure (CRITICAL):** The code must enforce a strict separation of concerns into a backend and frontend directory structure. Each microservice must have its own dedicated test suite. Additionally, it must incorporate robust testing, CI/CD, and quality gates:  
  ``` text
  project_root/  
  ├── .github/  
  │   └── workflows/  
  │       └── ci.yml                                # GitHub Actions pipeline for tests & linting 
  ├── _plans/
  │   └── implementation.md                         # Implementation plan for the project 
  ├── _reqs/
  │   ├── submission-strategy-part-1.md             # Product Requirements Document 
  ├── _docs/
  │   ├── adr/
  │   │   └── 0001-calculate-first-reason-second.md # Architecture Decision Record
  │   └── architecture.mmd                          # Mermaid.js architecture diagram
  ├── backend/
  │   ├── data/
  │   │   └── sales-data.csv                        # Bundled mock data for zero-click testing
  │   ├── tests/                                    # Backend unit & integration tests (pytest)
  │   │   └── test_evals.py                         # LLM validation & guardrail tests (deepeval)
  │   ├── api.py                                    # FastAPI application and routes
  │   ├── config.py                                 # Environment variable management via Starlette Config
  │   ├── schemas.py                                # Pydantic models for data validation
  │   ├── sop_engine.py                             # Pandas calculations
  │   ├── llm_service.py                            # Factory pattern for Local OpenAI vs Prod Anthropic
  │   ├── telemetry.py                              # OpenTelemetry, Structlog, & Langfuse setup
  │   ├── pyproject.toml                            # Backend dependencies managed by uv
  │   ├── uv.lock                                   # Backend dependency lockfile
  │   └── Dockerfile                                # FastAPI Dockerfile
  ├── frontend/
  │   ├── tests/                                    # Frontend UI tests (pytest + Streamlit AppTest)
  │   ├── app.py                                    # Streamlit UI (makes requests to FastAPI)
  │   ├── pyproject.toml                            # Frontend dependencies managed by uv
  │   ├── uv.lock                                   # Frontend dependency lockfile
  │   └── Dockerfile                                # Streamlit Dockerfile
  ├── .pre-commit-config.yaml                       # Pre-commit hooks configuration
  ├── Makefile                                      # Developer CLI commands wrapper
  ├── README.md                                     # Comprehensive project documentation
  └── docker-compose.yml                            # Orchestrates both services for local dev
  ```

## 5. Code Quality, Resiliency, Observability & Testing

To prove production-readiness, the following engineering standards are required:

- **Environment & Package Management (uv):** 
  - Use uv as the modern, high-performance package manager instead of standard pip and requirements.txt. All dependencies must be added using uv add and tracked in pyproject.toml and uv.lock.  
- **Configuration Management (Starlette):** 
  - Use starlette.config.Config inside backend/config.py to centrally load and manage all environment variables. Include a DATA_FILE_PATH variable (defaulting to data/sales-data.csv) so the backend knows where to read the CSV file from automatically.  
- **Observability (Otel & Langfuse):**  
  - Integrate **OpenTelemetry (Otel)** and **Structlog** to format all logs as structured JSON. Export traces and logs via OTLP to **HyperDX** for unified observability.  
  - Integrate **Langfuse** to wrap the LLM API calls. This must trace prompt inputs, model outputs, latency, and token consumption to provide AI-specific observability.  
- **Resiliency & Error Handling:**  
  - Use the tenacity library to wrap the LLM API call. It must include exponential backoff and retry logic (@retry(wait=wait_exponential...)) to handle rate limits or transient API timeouts gracefully.  
- **Data Validation:**  
  - Use pydantic in schemas.py to validate the CSV structure *after* Pandas loads it but *before* the calculations. If a column is missing or data types are wrong, the API must return a clean 500 Internal Server Error with a descriptive log.
- **LLM Evaluation & Guardrails (DeepEval):**  
  - Use deepeval to create an automated evaluation test for the LLM output.  
  - **The Test:** The test must extract the LLM's recommended "Air Freight SKU" from its generated Markdown and assert that it matches the Air_Freight_Candidate calculated deterministically by Pandas in Section 3.E. This proves the LLM is doing genuine reasoning and aligns with the mathematical ground truth.  
- **Makefile:** Provide a Makefile at the root to abstract standard workflows. Required commands:  
  - make test (runs pytest suite with coverage across both frontend and backend)  
  - make lint (runs formatters and linters)  
  - make pre-commit (installs/runs pre-commit hooks)  
  - make reqs (runs uv export --format requirements-txt > requirements.txt to generate lock files for Docker builds)  
  - make docker-build (must depend on make reqs, builds the individual docker images)  
  - make up (runs docker-compose up)  
- **Testing & Coverage:** Minimum **70% test coverage** is mandated for both the backend and frontend.  
  - Use pytest and pytest-cov.  
  - **Backend Tests:** The core calculations inside sop_engine.py (Pandas logic) must be heavily unit-tested with mocked data to prove the supply chain formulas work correctly, including edge cases like Projected_M5_Sales = 0 (Division by Zero). API endpoints should be tested using TestClient from FastAPI.  
  - **Frontend Tests:** The Streamlit UI should be tested using Streamlit's built-in AppTest framework via pytest to verify rendering and API integration behavior without needing a browser.  
- **Pre-Commit Hooks:** Include a .pre-commit-config.yaml. Before any code is committed, it must automatically pass:  
  - Code Formatting: black  
  - Linting: ruff  
  - Type Checking: mypy (ensure Python type hints are utilized in the codebase).  
- **GitHub Actions Workflow:** The .github/workflows/ci.yml must define a CI pipeline that triggers on all Pull Requests and pushes to main. It must set up uv, install dependencies via uv sync, run pre-commit, and run the test suites with coverage checks for both the backend and frontend.

## 6. API Backend (FastAPI)

- **Primary Endpoint:** GET /api/v1/generate-sop  
  - **Input:** No payload required. The endpoint automatically reads the CSV file from config("DATA_FILE_PATH").  
  - **Processing:** Passes the valid data to sop_engine.py for calculation and LLM generation.  
  - **Output:** Returns a JSON response matching this Pydantic schema:
    ```json
    {  
      "status": "success",  
      "metrics": {  
         "total_m4_revenue": 150000.00,  
         "skus_at_risk": 4  
      },  
      "red_flag_data": [...], // Array of objects containing at-risk SKU data  
      "llm_briefing": "Markdown string generated by the LLM..."  
    }
    ```
- **Action Endpoint:** GET /api/v1/download-pos  
  - **Output:** Exposes an endpoint that formats the calculated Suggested_Reorder_Qty values into a standard Purchase Order CSV format. This returns a downloadable CSV payload of all SKUs where the reorder quantity is greater than 0, ready to be uploaded to an inventory system like Cin7.

## 7. UI & Delivery Mechanism (Streamlit App)

The app.py Streamlit frontend is strictly a frictionless presentation layer. It must NOT contain business logic and should not require the user to upload a file or click any buttons to see the data.

- Provide a clean UI that **automatically** triggers the generation process on initial load (use @st.cache_data to ensure the API is only hit once per session/cache expiry, rather than on every UI re-render).  
- Send a GET request to the FastAPI backend URL (configurable via env vars) as soon as the page opens.  
- While waiting for the backend, show a professional loading state (e.g., st.spinner("Analyzing omnichannel data and generating S&OP insights...")).  
- Display the LLM-generated briefing in the main content area using st.markdown().  
- Use the metrics and red_flag_data from the JSON response to render top-level KPI widgets and a Pandas dataframe showing the SKUs at risk below the briefing. Include robust error handling if the API fails.
- **Actionability Feature:** Add a prominent "Download Draft POs (CSV)" button using st.download_button that interfaces with the /api/v1/download-pos endpoint. This transforms the dashboard from a passive report into an active workflow driver, allowing executives to act immediately on the insights.

## 8. LLM Orchestration & Engineering (Inside Backend)

**Local vs. Production Environments (Factory Pattern):**

To avoid introducing heavyweight abstraction layers (like LiteLLM proxies) while still supporting local development, the application must use a Factory pattern in llm_service.py:

- **If ENV=local:** Initialize the standard openai python SDK configured with a base_url pointing to http://localhost:1234/v1 (LM Studio) or http://localhost:8000/v1 (vLLM).  
- **If ENV=production:** Initialize the native anthropic python SDK to utilize Claude 3.5 Sonnet.

**System Prompt:**

"You are an expert Supply Chain & S&OP Director for a highly successful DTC honey brand. Your task is to review the weekly pre-calculated inventory data and write a concise, highly actionable S&OP briefing for the executive team. The briefing must take under 5 minutes to read. Use a professional, data-driven, yet accessible tone. Use Markdown formatting for readability."

**User Prompt:**

"Below is the JSON payload containing the analyzed sales and inventory data for this week. It includes total sales, projected demand (accounting for MoM growth), stock risks, and mathematically calculated reorder quantities.

1. Write an Executive Summary of the past month's performance.  
2. Highlight what sold well vs. what sold poorly. Specifically, call out the worst-performing SKU (dead stock) and reason about whether we should implement a discount or bundling strategy to free up working capital.  
3. Create a 'Red Flags' section for SKUs falling below target cover. Note that projections are based on trailing 4-month momentum; explicitly remind the team to consider upcoming seasonality factors.  
4. Make reorder recommendations for at least 3 SKUs. Use the 'Suggested_Reorder_Qty' provided, but write out the genuine business reasoning for *why* we are ordering that amount (e.g., referencing lead times, current pipeline, and target cover). If multiple items need reordering, prioritize them: reason about which one is the highest priority based on its revenue contribution (Revenue_M4) vs. its lead time, assuming a constrained cash-flow environment.  
5. Acknowledge the 'Bioactive Blend' line as new Q1 2026 products. Explain to the team that to avoid over-ordering on an initial launch spike, we have conservatively modeled their future demand using their current M4 baseline rather than compounding their initial MoM growth.
6. **Strategic Priority (Air Freight):** Based on the data provided, identify the single most critical SKU that is currently at risk. Weigh its recent revenue contribution (Revenue_M4) against its stock risk. Make a recommendation on whether we should pay a premium to air-freight this specific item to protect top-line revenue and justify your choice logically.

DATA PAYLOAD:

{json_payload_string}"

## 9. Deployment, Documentation & Containerization (Docker + Fly.io)

To demonstrate production-ready engineering, the project must include a docker-compose.yml for seamless local testing, and individual Dockerfiles for cloud deployment.

- **Documentation (README.md):** Must include a comprehensive README with setup instructions, environment variable requirements, and a Mermaid.js diagram illustrating the microservices architecture (including the logging/tracing flow to HyperDX and Langfuse).  
- **Backend Dockerfile:** Must copy the data/ directory into the container, copy the requirements.txt generated by the make reqs command to install dependencies, and then run uvicorn api:app --host 0.0.0.0 --port 8000.  
- **Frontend Dockerfile:** Must copy the requirements.txt generated by the make reqs command, install dependencies, and run streamlit run app.py --server.address 0.0.0.0 --server.port 8501.

*Deployment Environment Variables (Required for Backend):*

* ENV (local or production)  
* DATA_FILE_PATH (defaults to data/sales-data.csv)  
* ANTHROPIC_API_KEY (Required for prod)  
* OTEL_EXPORTER_OTLP_ENDPOINT (e.g., HyperDX endpoint)  
* OTEL_EXPORTER_OTLP_HEADERS (e.g., HyperDX API key)  
* LANGFUSE_PUBLIC_KEY  
* LANGFUSE_SECRET_KEY  
* LANGFUSE_HOST

*Deployment Instructions for Interview Demo (Fly.io):*

Deploying microservices on Fly.io involves creating two separate apps.

1. cd backend && fly launch (set API keys and telemetry secrets).  
2. cd frontend && fly launch (set BACKEND_URL environment variable to point to the backend Fly.io internal/external URL).

## 10. Documentation Standards

The generated repository must adhere to the following documentation standards:

- **Robust README.md**: Must be highly polished and include:  
  - **Badges**: CI/CD build status, test coverage percentage, and Python version compatibility via shields.io.  
  - **Project Overview**: A TL;DR of the architecture and business purpose.  
  - **Architecture Diagram**: An embedded Mermaid.js (architecture.mmd) diagram visually mapping the data flow (CSV -> FastAPI + Pandas -> Langfuse/Anthropic -> Streamlit).  
  - **Setup Instructions**: Explicit, copy-pasteable commands for both local development using uv and containerized workflows via docker-compose.  
  - **Environment Variables Table**: A Markdown table documenting every required variable, its purpose, and its default fallback value.  
- **Architecture Decision Records (ADR)**: Include a docs/adr/0001-calculate-first-reason-second.md file. This document must explicitly outline the decision to separate the Pandas math logic from the LLM prompt, explaining the risks of LLM arithmetic hallucinations and the benefit of deterministic supply chain formulas.  
- **API Documentation (Swagger UI)**: Ensure FastAPI is configured to auto-generate the OpenAPI schema so the hiring manager can test the backend directly at the /docs endpoint.  
- **Inline Code Documentation**:  
  - Enforce Google-style Docstrings for all classes and functions.  
  - All functions must utilize explicit Python type hints (validated by mypy in the pre-commit hook).

## Clarifying Questions

**Q: The deepeval test must "extract the LLM's recommended Air Freight SKU from its generated Markdown." Should the LLM prompt be written to produce a clearly delimited section (e.g., a specific header or bold label) to make regex extraction reliable, or is free-form parsing acceptable?**
A: clearly delimited section

**Q: Is actual cloud deployment to Fly.io required for the submission, or is a working local `docker-compose up` sufficient for the hiring manager to evaluate the project?**
A: Should be deployable to Fly.io for the hiring manager to evaluate the project.

**Q: For Langfuse and HyperDX observability: should these be configured against real cloud accounts (requiring API keys), or is a locally self-hosted Langfuse (via Docker) plus a stubbed/no-op OTLP exporter acceptable for local dev and the submission demo?**
A: locally self-hosted with a strecth goal to connect to real cloud accounts.

**Q: The reqs specify "Claude 3.5 Sonnet" for production. Should the model ID be pinned to `claude-3-5-sonnet-20241022`, or is using the latest available Sonnet model (currently `claude-sonnet-4-6`) preferred?**
A: latest available

**Q: For the `GET /api/v1/download-pos` endpoint, should it return all SKUs where `Suggested_Reorder_Qty > 0`, or only the subset explicitly discussed/recommended in the LLM briefing?**
A: return all SKUs where `Suggested_Reorder_Qty > 0`

**Q: What should the default `BACKEND_URL` be in the frontend for local development outside of Docker (i.e., when running `uv run streamlit run app.py` directly)? `http://localhost:8000`?**
A: http://localhost:8000 works for the backend
