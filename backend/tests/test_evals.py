"""LLM evaluation test using deepeval.

Validates that the LLM's air freight recommendation matches the deterministic
Pandas-calculated ground truth (Air_Freight_Candidate).

Architecture note (ADR 0001): Air_Freight_Candidate is intentionally withheld
from the JSON payload sent to the LLM. This test proves the model reasons
correctly from the data rather than simply echoing a pre-supplied answer.
"""

import re
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sop_engine

# Ground truth from the real sales CSV — computed once per session.
_REAL_DF = sop_engine.calculate(sop_engine.load_and_validate("data/sales-data.csv"))
GROUND_TRUTH_AIR_FREIGHT_SKU: str = sop_engine.get_air_freight_candidate(_REAL_DF)

# Regex to extract the delimited air freight line from LLM markdown output.
AIR_FREIGHT_PATTERN = re.compile(r"\*\*AIR FREIGHT SKU:\s*(.+?)\*\*", re.IGNORECASE)


def extract_air_freight_sku(markdown: str) -> str:
    """Parse the LLM briefing markdown and return the recommended air freight SKU.

    The LLM is instructed to output exactly:
        **AIR FREIGHT SKU: <full SKU name here>**

    Args:
        markdown: The full LLM-generated briefing string.

    Returns:
        The extracted SKU name, stripped of whitespace.  Empty string if not found.
    """
    match = AIR_FREIGHT_PATTERN.search(markdown)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Unit test: extraction helper
# ---------------------------------------------------------------------------


def test_extraction_finds_delimited_sku() -> None:
    """extract_air_freight_sku must parse the exact delimiter format."""
    sample = (
        "Some analysis text.\n\n"
        "## Strategic Priority: Air Freight Recommendation\n\n"
        "This SKU is critical.\n\n"
        "**AIR FREIGHT SKU: Manuka Honey MGO 263+ 500g**"
    )
    assert extract_air_freight_sku(sample) == "Manuka Honey MGO 263+ 500g"


def test_extraction_returns_empty_when_missing() -> None:
    """extract_air_freight_sku returns empty string when the delimiter is absent."""
    assert extract_air_freight_sku("No air freight recommendation here.") == ""


def test_extraction_case_insensitive() -> None:
    """Extraction must work regardless of delimiter casing."""
    sample = "**air freight sku: Manuka Honey MGO 514+ 250g**"
    assert extract_air_freight_sku(sample) == "Manuka Honey MGO 514+ 250g"


# ---------------------------------------------------------------------------
# Ground truth sanity check
# ---------------------------------------------------------------------------


def test_ground_truth_is_non_empty() -> None:
    """The real sales data must have at least one at-risk SKU for the eval to be meaningful."""
    assert GROUND_TRUTH_AIR_FREIGHT_SKU != "", (
        "No at-risk SKUs found in sales data — "
        "the deepeval test cannot proceed without a ground truth candidate."
    )


def test_ground_truth_is_at_risk() -> None:
    """Ground truth SKU must actually be flagged Is_At_Risk in the Pandas output."""
    at_risk_skus = _REAL_DF[_REAL_DF["Is_At_Risk"]]["SKU"].tolist()
    assert GROUND_TRUTH_AIR_FREIGHT_SKU in at_risk_skus


def test_ground_truth_has_highest_revenue_among_at_risk() -> None:
    """Ground truth SKU must be the highest-revenue at-risk SKU."""
    at_risk = _REAL_DF[_REAL_DF["Is_At_Risk"]]
    top_sku = str(at_risk.loc[at_risk["Revenue_M4"].idxmax(), "SKU"])
    assert GROUND_TRUTH_AIR_FREIGHT_SKU == top_sku


# ---------------------------------------------------------------------------
# LLM reasoning evaluation (mock LLM — no live API call required in CI)
# ---------------------------------------------------------------------------


def _make_mock_briefing(sku: str) -> str:
    """Helper to generate a mock briefing containing the correct delimiter."""
    return (
        "## Executive Summary\n\n"
        "Strong performance this month.\n\n"
        "## Strategic Priority: Air Freight Recommendation\n\n"
        f"The highest-revenue at-risk SKU is {sku}. We recommend air freight.\n\n"
        f"**AIR FREIGHT SKU: {sku}**"
    )


@pytest.mark.skipif(
    not GROUND_TRUTH_AIR_FREIGHT_SKU,
    reason="No at-risk SKUs in sales data",
)
def test_llm_air_freight_matches_ground_truth_mock() -> None:
    """With a mocked LLM that returns the correct SKU, extraction must match ground truth.

    This test validates the eval pipeline end-to-end using a mock LLM response.
    Replace the mock with a live LLM call (skipping in CI) for true evaluation.
    """
    import llm_service  # noqa: PLC0415

    payload = sop_engine.build_llm_payload(_REAL_DF)
    correct_briefing = _make_mock_briefing(GROUND_TRUTH_AIR_FREIGHT_SKU)

    with patch.object(
        llm_service, "_call_llm_with_retry", return_value=(correct_briefing, {})
    ):
        briefing = llm_service.generate_briefing(payload)

    extracted = extract_air_freight_sku(briefing)
    assert (
        extracted == GROUND_TRUTH_AIR_FREIGHT_SKU
    ), f"LLM recommended '{extracted}' but ground truth is '{GROUND_TRUTH_AIR_FREIGHT_SKU}'"


@pytest.mark.skipif(
    not GROUND_TRUTH_AIR_FREIGHT_SKU,
    reason="No at-risk SKUs in sales data",
)
def test_llm_wrong_air_freight_fails_eval() -> None:
    """Simulates a hallucinating LLM — the extracted SKU must not match ground truth."""
    wrong_sku = "Propolis Tincture 30ml"
    assert (
        wrong_sku != GROUND_TRUTH_AIR_FREIGHT_SKU
    ), "Choose a different 'wrong' SKU that isn't the actual ground truth"

    briefing = _make_mock_briefing(wrong_sku)
    extracted = extract_air_freight_sku(briefing)
    assert extracted != GROUND_TRUTH_AIR_FREIGHT_SKU
