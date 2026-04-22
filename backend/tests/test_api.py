"""Integration tests for the FastAPI endpoints using TestClient.

Post-graph conversion, `/generate-sop` goes through an openarmature
`CompiledGraph` that is built during FastAPI lifespan startup. TestClient
must therefore be used as a context manager (`with TestClient(app) as c:`)
so the lifespan handler fires and `api._graph` is populated. The `client`
fixture below handles that for every test.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Patch telemetry.setup() before importing api to avoid side effects.
with patch("telemetry.setup"):
    from api import app

MOCK_BRIEFING = (
    "## Executive Summary\n\nTest briefing.\n\n"
    "**AIR FREIGHT SKU: Daily Wellness Tier 2 500g**"
)

MOCK_DF = pd.DataFrame(
    [
        {
            "SKU": "Daily Wellness Tier 2 500g",
            "Total_M1": 556,
            "Total_M2": 596,
            "Total_M3": 628,
            "Total_M4": 684,
            "Revenue_M4": 37613.16,
            "MoM_Growth_Avg": 0.07,
            "Projected_M5_Sales": 732.0,
            "Stock_On_Hand": 1700,
            "Units_On_Order": 0,
            "Order_Arrival_Months": 0,
            "Target_Months_Cover": 2,
            "Current_Months_Cover": 1.32,
            "Effective_Months_Cover": 1.32,
            "Is_At_Risk": True,
            "Suggested_Reorder_Qty": 764,
            "Retail_Price_USD": 54.99,
        }
    ]
)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """TestClient that fires FastAPI lifespan startup (compiles the graph)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mock_engine_and_llm() -> Iterator[None]:
    """Patch sop_engine and llm_service so no CSV or LLM call is needed.

    Patching at the module where the function is *defined* (`sop_engine`,
    `llm_service`) affects every importer — both `api.py` (direct call in
    `/download-pos`) and `graph.py` (nodes invoked by `/generate-sop`).
    """
    with (
        patch("sop_engine.load_and_validate", return_value=MOCK_DF),
        patch("sop_engine.calculate", return_value=MOCK_DF),
        patch("sop_engine.build_llm_payload", return_value={"all_skus": []}),
        patch("llm_service.generate_briefing", return_value=MOCK_BRIEFING),
    ):
        yield


def test_generate_sop_returns_200(client: TestClient, mock_engine_and_llm: None) -> None:
    """GET /api/v1/generate-sop must return 200 with a valid SOPResponse shape."""
    response = client.get("/api/v1/generate-sop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert "metrics" in body
    assert "red_flag_data" in body
    assert "llm_briefing" in body


def test_generate_sop_metrics_shape(client: TestClient, mock_engine_and_llm: None) -> None:
    """Metrics in the response must include total_m4_revenue and skus_at_risk."""
    response = client.get("/api/v1/generate-sop")
    metrics = response.json()["metrics"]
    assert "total_m4_revenue" in metrics
    assert "skus_at_risk" in metrics


def test_generate_sop_briefing_passed_through(
    client: TestClient, mock_engine_and_llm: None
) -> None:
    """The briefing returned from the graph reaches the response body intact."""
    response = client.get("/api/v1/generate-sop")
    assert response.json()["llm_briefing"] == MOCK_BRIEFING


def test_generate_sop_csv_not_found(client: TestClient) -> None:
    """Returns 500 when the CSV file is missing.

    Post-conversion, the FileNotFoundError surfaces inside `load_node`; the
    engine wraps it as `NodeException`, which the handler catches as
    `RuntimeGraphError`. Response body therefore mentions the wrapper class.
    """
    with patch("sop_engine.load_and_validate", side_effect=FileNotFoundError):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500
    assert "NodeException" in response.json()["detail"]


def test_generate_sop_csv_validation_error(client: TestClient) -> None:
    """Returns 500 when the CSV fails Pydantic validation."""
    with patch("sop_engine.load_and_validate", side_effect=ValueError("bad column")):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500
    assert "NodeException" in response.json()["detail"]


def test_generate_sop_llm_error(client: TestClient, mock_engine_and_llm: None) -> None:
    """Returns 500 when the LLM call fails."""
    with patch("llm_service.generate_briefing", side_effect=RuntimeError("timeout")):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500
    assert "NodeException" in response.json()["detail"]


def test_download_pos_returns_csv(client: TestClient, mock_engine_and_llm: None) -> None:
    """GET /api/v1/download-pos must return a CSV file attachment."""
    response = client.get("/api/v1/download-pos")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "draft-purchase-orders.csv" in response.headers["content-disposition"]


def test_download_pos_csv_contains_sku(client: TestClient, mock_engine_and_llm: None) -> None:
    """The PO CSV must contain the at-risk SKU header and a data row."""
    response = client.get("/api/v1/download-pos")
    content = response.content.decode()
    assert "SKU" in content
    assert "Daily Wellness Tier 2 500g" in content


def test_download_pos_csv_not_found(client: TestClient) -> None:
    """Returns 500 when the CSV file is missing for PO download."""
    with patch("sop_engine.load_and_validate", side_effect=FileNotFoundError):
        response = client.get("/api/v1/download-pos")
    assert response.status_code == 500


def test_openapi_docs_accessible(client: TestClient) -> None:
    """The OpenAPI /docs endpoint must be reachable (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_accessible(client: TestClient) -> None:
    """The OpenAPI schema /openapi.json must be reachable."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "paths" in schema
    assert "/api/v1/generate-sop" in schema["paths"]
    assert "/api/v1/download-pos" in schema["paths"]
