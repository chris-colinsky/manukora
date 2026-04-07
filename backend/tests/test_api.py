"""Integration tests for the FastAPI endpoints using TestClient."""

import sys
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Patch telemetry.setup() before importing api to avoid side effects in tests.
with patch("telemetry.setup"):
    from api import app

client = TestClient(app)

MOCK_SOP_RESPONSE = {
    "status": "success",
    "metrics": {"total_m4_revenue": 150000.0, "skus_at_risk": 3},
    "red_flag_data": [
        {
            "SKU": "Manuka Honey MGO 263+ 500g",
            "Effective_Months_Cover": 1.3,
            "Target_Months_Cover": 2,
            "Suggested_Reorder_Qty": 500,
            "Revenue_M4": 37513.84,
        }
    ],
    "llm_briefing": "## Executive Summary\n\nTest briefing.\n\n**AIR FREIGHT SKU: Manuka Honey MGO 263+ 500g**",
}


@pytest.fixture()
def mock_engine_and_llm():
    """Patch sop_engine and llm_service so no CSV or LLM call is needed."""
    import pandas as pd

    mock_df = pd.DataFrame(
        [
            {
                "SKU": "Manuka Honey MGO 263+ 500g",
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

    with (
        patch("api.sop_engine.load_and_validate", return_value=mock_df),
        patch("api.sop_engine.calculate", return_value=mock_df),
        patch("api.sop_engine.build_llm_payload", return_value={"all_skus": []}),
        patch(
            "api.llm_service.generate_briefing",
            return_value=MOCK_SOP_RESPONSE["llm_briefing"],
        ),
    ):
        yield


def test_generate_sop_returns_200(mock_engine_and_llm) -> None:  # type: ignore[no-untyped-def]
    """GET /api/v1/generate-sop must return 200 with a valid SOPResponse shape."""
    response = client.get("/api/v1/generate-sop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert "metrics" in body
    assert "red_flag_data" in body
    assert "llm_briefing" in body


def test_generate_sop_metrics_shape(mock_engine_and_llm) -> None:  # type: ignore[no-untyped-def]
    """Metrics in the response must include total_m4_revenue and skus_at_risk."""
    response = client.get("/api/v1/generate-sop")
    metrics = response.json()["metrics"]
    assert "total_m4_revenue" in metrics
    assert "skus_at_risk" in metrics


def test_generate_sop_csv_not_found() -> None:
    """Returns 500 when the CSV file is missing."""
    with patch("api.sop_engine.load_and_validate", side_effect=FileNotFoundError):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500
    assert "not found" in response.json()["detail"].lower()


def test_generate_sop_csv_validation_error() -> None:
    """Returns 500 when the CSV fails Pydantic validation."""
    with patch(
        "api.sop_engine.load_and_validate", side_effect=ValueError("bad column")
    ):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500
    assert "validation" in response.json()["detail"].lower()


def test_generate_sop_llm_error(mock_engine_and_llm) -> None:  # type: ignore[no-untyped-def]
    """Returns 500 when the LLM call fails."""
    with patch(
        "api.llm_service.generate_briefing", side_effect=RuntimeError("timeout")
    ):
        response = client.get("/api/v1/generate-sop")
    assert response.status_code == 500


def test_download_pos_returns_csv(mock_engine_and_llm) -> None:  # type: ignore[no-untyped-def]
    """GET /api/v1/download-pos must return a CSV file attachment."""
    response = client.get("/api/v1/download-pos")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "draft-purchase-orders.csv" in response.headers["content-disposition"]


def test_download_pos_csv_contains_sku(mock_engine_and_llm) -> None:  # type: ignore[no-untyped-def]
    """The PO CSV must contain the at-risk SKU header and a data row."""
    response = client.get("/api/v1/download-pos")
    content = response.content.decode()
    assert "SKU" in content
    # The mock SKU has Suggested_Reorder_Qty > 0, so it must appear in the CSV.
    assert "Manuka Honey MGO 263+ 500g" in content


def test_download_pos_csv_not_found() -> None:
    """Returns 500 when the CSV file is missing for PO download."""
    with patch("api.sop_engine.load_and_validate", side_effect=FileNotFoundError):
        response = client.get("/api/v1/download-pos")
    assert response.status_code == 500


def test_openapi_docs_accessible() -> None:
    """The OpenAPI /docs endpoint must be reachable (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_accessible() -> None:
    """The OpenAPI schema /openapi.json must be reachable."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "paths" in schema
    assert "/api/v1/generate-sop" in schema["paths"]
    assert "/api/v1/download-pos" in schema["paths"]
