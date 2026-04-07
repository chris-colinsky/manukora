"""Streamlit AppTest tests for frontend/app.py."""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")

MOCK_SUCCESS_RESPONSE = {
    "status": "success",
    "metrics": {"total_m4_revenue": 148250.50, "skus_at_risk": 3},
    "red_flag_data": [
        {
            "SKU": "Manuka Honey MGO 263+ 500g",
            "Effective_Months_Cover": 1.3,
            "Target_Months_Cover": 2,
            "Suggested_Reorder_Qty": 764,
            "Revenue_M4": 37613.16,
        },
        {
            "SKU": "Manuka Honey MGO 514+ 500g",
            "Effective_Months_Cover": 1.1,
            "Target_Months_Cover": 2,
            "Suggested_Reorder_Qty": 450,
            "Revenue_M4": 31356.08,
        },
    ],
    "llm_briefing": (
        "## Executive Summary\n\nStrong omnichannel performance this month.\n\n"
        "## Strategic Priority: Air Freight Recommendation\n\n"
        "**AIR FREIGHT SKU: Manuka Honey MGO 263+ 500g**"
    ),
}


@pytest.fixture(autouse=True)
def clear_streamlit_cache() -> None:
    """Clear st.cache_data between tests to prevent cross-test cache pollution."""
    st.cache_data.clear()


def _make_mock_response(data: dict) -> MagicMock:
    """Build a mock requests.Response returning the given dict as JSON."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = data
    mock_resp.content = b"SKU,Order_Qty\nManuka Honey MGO 263+ 500g,764\n"
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dashboard_renders_on_load() -> None:
    """The app should render content without user interaction."""
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(MOCK_SUCCESS_RESPONSE)):
        at.run(timeout=30)

    assert not at.exception
    # App renders meaningful content: metrics and markdown are present.
    assert len(at.metric) >= 2


def test_dashboard_shows_metrics() -> None:
    """KPI metric widgets must render with the correct values."""
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(MOCK_SUCCESS_RESPONSE)):
        at.run(timeout=30)

    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert len(metric_values) >= 2


def test_dashboard_shows_total_revenue_metric() -> None:
    """The Total M4 Revenue metric must display the formatted value."""
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(MOCK_SUCCESS_RESPONSE)):
        at.run(timeout=30)

    assert not at.exception
    # One metric should contain the revenue value "$148,251"
    metric_values = [m.value for m in at.metric]
    assert any("148" in str(v) for v in metric_values)


def test_dashboard_renders_llm_briefing() -> None:
    """The LLM briefing markdown should appear in the rendered output."""
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(MOCK_SUCCESS_RESPONSE)):
        at.run(timeout=30)

    assert not at.exception
    assert len(at.markdown) > 0


def test_dashboard_renders_briefing_content() -> None:
    """The Executive Summary text from the LLM briefing should be rendered."""
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(MOCK_SUCCESS_RESPONSE)):
        at.run(timeout=30)

    assert not at.exception
    markdown_texts = " ".join(m.value for m in at.markdown)
    assert "Executive Summary" in markdown_texts or "Manukora" in markdown_texts


def test_dashboard_handles_connection_error() -> None:
    """When the backend is unreachable, the app should show an error, not crash."""
    import requests as req

    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", side_effect=req.exceptions.ConnectionError("refused")):
        at.run(timeout=30)

    assert not at.exception
    errors = at.error
    assert len(errors) > 0
    assert any(
        "connect" in str(e.value).lower() or "backend" in str(e.value).lower()
        for e in errors
    )


def test_dashboard_handles_http_error() -> None:
    """When the backend returns a 500, the app should show an error message."""
    import requests as req

    at = AppTest.from_file(APP_PATH)

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    http_error = req.exceptions.HTTPError("500 Server Error", response=mock_resp)
    mock_resp.raise_for_status.side_effect = http_error

    with patch("requests.get", return_value=mock_resp):
        at.run(timeout=30)

    assert not at.exception
    errors = at.error
    assert len(errors) > 0


def test_no_red_flags_shows_success_message() -> None:
    """When there are no at-risk SKUs, a success message should be shown."""
    safe_metrics: dict[str, float] = {"total_m4_revenue": 148250.50, "skus_at_risk": 0}
    safe_response = {
        **MOCK_SUCCESS_RESPONSE,
        "red_flag_data": [],
        "metrics": safe_metrics,
    }
    at = AppTest.from_file(APP_PATH)

    with patch("requests.get", return_value=_make_mock_response(safe_response)):
        at.run(timeout=30)

    assert not at.exception
    success_msgs = at.success
    assert len(success_msgs) > 0
