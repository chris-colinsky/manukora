"""Unit tests for sop_engine.py — all supply chain calculation formulas."""

import io

import pandas as pd
import pytest

import sop_engine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CSV = """\
SKU,Shopify_Units_M1,Shopify_Units_M2,Shopify_Units_M3,Shopify_Units_M4,Amazon_Units_M1,Amazon_Units_M2,Amazon_Units_M3,Amazon_Units_M4,Stock_On_Hand,Units_On_Order,Order_Arrival_Months,Target_Months_Cover,Retail_Price_USD
Alpha,100,110,120,130,50,55,60,65,500,200,1,2,10.00
Beta,200,180,160,140,100,90,80,70,2000,0,0,2,20.00
Bioactive Blend Immunity 250g,100,120,144,173,50,60,72,86,600,200,1,2,15.00
ZeroSales,0,0,0,0,0,0,0,0,100,0,0,2,5.00
AtRisk,80,85,90,95,40,42,45,48,100,0,1,2,50.00
"""


@pytest.fixture()
def base_df() -> pd.DataFrame:
    """Minimal validated DataFrame for formula testing."""
    df = pd.read_csv(io.StringIO(MINIMAL_CSV))
    return sop_engine.calculate(df)


# ---------------------------------------------------------------------------
# A. Omni-channel totals
# ---------------------------------------------------------------------------


def test_omnichannel_totals(base_df: pd.DataFrame) -> None:
    """Total_M1-4 should be the sum of Shopify and Amazon channels."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    assert alpha["Total_M1"] == 150  # 100 + 50
    assert alpha["Total_M2"] == 165
    assert alpha["Total_M3"] == 180
    assert alpha["Total_M4"] == 195


def test_revenue_m4(base_df: pd.DataFrame) -> None:
    """Revenue_M4 should be Total_M4 * Retail_Price_USD."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    assert alpha["Revenue_M4"] == pytest.approx(195 * 10.00)


# ---------------------------------------------------------------------------
# B. Growth & projections
# ---------------------------------------------------------------------------


def test_mom_growth_positive(base_df: pd.DataFrame) -> None:
    """Alpha has consistent 10% growth; MoM_Growth_Avg should be positive."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    assert alpha["MoM_Growth_Avg"] > 0


def test_mom_growth_negative(base_df: pd.DataFrame) -> None:
    """Beta is declining; MoM_Growth_Avg should be negative."""
    beta = base_df[base_df["SKU"] == "Beta"].iloc[0]
    assert beta["MoM_Growth_Avg"] < 0


def test_projected_m5_not_negative(base_df: pd.DataFrame) -> None:
    """Projected_M5_Sales must never be negative."""
    assert (base_df["Projected_M5_Sales"] >= 0).all()


def test_bioactive_blend_projection_uses_m4_baseline(base_df: pd.DataFrame) -> None:
    """Bioactive Blend SKUs must use Total_M4 as Projected_M5 (not compounded growth)."""
    bio = base_df[base_df["SKU"] == "Bioactive Blend Immunity 250g"].iloc[0]
    assert bio["Projected_M5_Sales"] == pytest.approx(bio["Total_M4"])


def test_non_bioactive_projection_uses_growth(base_df: pd.DataFrame) -> None:
    """Non-Bioactive Blend SKUs must use the compounded MoM growth projection."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    expected = alpha["Total_M4"] * (1 + alpha["MoM_Growth_Avg"])
    assert alpha["Projected_M5_Sales"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# C. Stock cover & division-by-zero edge case
# ---------------------------------------------------------------------------


def test_stock_cover_normal(base_df: pd.DataFrame) -> None:
    """Current_Months_Cover should be Stock_On_Hand / Projected_M5_Sales."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    expected_current = alpha["Stock_On_Hand"] / alpha["Projected_M5_Sales"]
    assert alpha["Current_Months_Cover"] == pytest.approx(expected_current)


def test_effective_cover_includes_on_order(base_df: pd.DataFrame) -> None:
    """Effective_Months_Cover should include Units_On_Order."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    expected = (alpha["Stock_On_Hand"] + alpha["Units_On_Order"]) / alpha[
        "Projected_M5_Sales"
    ]
    assert alpha["Effective_Months_Cover"] == pytest.approx(expected)


def test_zero_projected_sales_sets_stagnant_cover(base_df: pd.DataFrame) -> None:
    """When Projected_M5_Sales == 0, cover values must be STAGNANT_COVER_VALUE (999)."""
    zero = base_df[base_df["SKU"] == "ZeroSales"].iloc[0]
    assert zero["Projected_M5_Sales"] == 0
    assert zero["Current_Months_Cover"] == sop_engine.STAGNANT_COVER_VALUE
    assert zero["Effective_Months_Cover"] == sop_engine.STAGNANT_COVER_VALUE


def test_zero_projected_is_not_at_risk(base_df: pd.DataFrame) -> None:
    """Stagnant SKUs (Projected_M5 == 0) must not be flagged Is_At_Risk."""
    zero = base_df[base_df["SKU"] == "ZeroSales"].iloc[0]
    assert zero["Is_At_Risk"] is False or not zero["Is_At_Risk"]


def test_is_at_risk_flag(base_df: pd.DataFrame) -> None:
    """Is_At_Risk must be True when Effective_Months_Cover < Target_Months_Cover."""
    for _, row in base_df.iterrows():
        if row["Projected_M5_Sales"] == 0:
            continue
        expected = row["Effective_Months_Cover"] < row["Target_Months_Cover"]
        assert bool(row["Is_At_Risk"]) == expected, f"Mismatch for SKU {row['SKU']}"


# ---------------------------------------------------------------------------
# D. Reorder quantities
# ---------------------------------------------------------------------------


def test_suggested_reorder_never_negative(base_df: pd.DataFrame) -> None:
    """Suggested_Reorder_Qty must never be negative."""
    assert (base_df["Suggested_Reorder_Qty"] >= 0).all()


def test_suggested_reorder_formula(base_df: pd.DataFrame) -> None:
    """Reorder qty = MAX(0, Total_Pipeline_Needed - Stock_On_Hand - Units_On_Order)."""
    alpha = base_df[base_df["SKU"] == "Alpha"].iloc[0]
    pipeline_needed = (
        alpha["Target_Months_Cover"] + alpha["Order_Arrival_Months"]
    ) * alpha["Projected_M5_Sales"]
    current_pipeline = alpha["Stock_On_Hand"] + alpha["Units_On_Order"]
    expected = max(0, pipeline_needed - current_pipeline)
    assert alpha["Suggested_Reorder_Qty"] == pytest.approx(int(expected))


# ---------------------------------------------------------------------------
# E. Poor performers
# ---------------------------------------------------------------------------


def test_poor_performers_filter(base_df: pd.DataFrame) -> None:
    """Beta is declining with large cover — should appear in poor performers."""
    poor = sop_engine.get_poor_performers(base_df)
    assert "Beta" in poor["SKU"].values


def test_growing_sku_not_poor_performer(base_df: pd.DataFrame) -> None:
    """Alpha is growing — must not appear in poor performers."""
    poor = sop_engine.get_poor_performers(base_df)
    assert "Alpha" not in poor["SKU"].values


# ---------------------------------------------------------------------------
# F. Air Freight Candidate (ground truth — NOT in LLM payload)
# ---------------------------------------------------------------------------


def test_air_freight_candidate_is_highest_revenue_at_risk(
    base_df: pd.DataFrame,
) -> None:
    """Air freight candidate must be the at-risk SKU with the highest Revenue_M4."""
    at_risk = base_df[base_df["Is_At_Risk"]]
    if at_risk.empty:
        pytest.skip("No at-risk SKUs in test data")
    expected = at_risk.loc[at_risk["Revenue_M4"].idxmax(), "SKU"]
    assert sop_engine.get_air_freight_candidate(base_df) == expected


def test_air_freight_candidate_empty_when_no_risk() -> None:
    """Returns empty string when no SKUs are at risk."""
    csv = """\
SKU,Shopify_Units_M1,Shopify_Units_M2,Shopify_Units_M3,Shopify_Units_M4,Amazon_Units_M1,Amazon_Units_M2,Amazon_Units_M3,Amazon_Units_M4,Stock_On_Hand,Units_On_Order,Order_Arrival_Months,Target_Months_Cover,Retail_Price_USD
SafeSKU,100,100,100,100,50,50,50,50,10000,5000,1,2,10.00
"""
    df = pd.read_csv(io.StringIO(csv))
    calculated = sop_engine.calculate(df)
    assert sop_engine.get_air_freight_candidate(calculated) == ""


# ---------------------------------------------------------------------------
# G. LLM payload construction
# ---------------------------------------------------------------------------


def test_llm_payload_excludes_air_freight_candidate(base_df: pd.DataFrame) -> None:
    """The LLM payload must NOT include an Air_Freight_Candidate key."""
    payload = sop_engine.build_llm_payload(base_df)
    payload_str = str(payload)
    assert "Air_Freight_Candidate" not in payload_str


def test_llm_payload_structure(base_df: pd.DataFrame) -> None:
    """Payload must have all_skus, skus_at_risk, and poor_performers keys."""
    payload = sop_engine.build_llm_payload(base_df)
    assert "all_skus" in payload
    assert "skus_at_risk" in payload
    assert "poor_performers" in payload


# ---------------------------------------------------------------------------
# H. CSV loading & validation
# ---------------------------------------------------------------------------


def test_load_and_validate_valid_csv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """load_and_validate should succeed with the real sales data."""
    df = sop_engine.load_and_validate("data/sales-data.csv")
    assert len(df) == 12  # 12 SKUs in mock data


def test_load_and_validate_missing_column(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """load_and_validate should raise ValueError if a required column is missing."""
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("SKU,Shopify_Units_M1\nAlpha,100\n")
    with pytest.raises(ValueError, match="missing required columns"):
        sop_engine.load_and_validate(str(bad_csv))


def test_load_and_validate_file_not_found() -> None:
    """load_and_validate should raise FileNotFoundError for a missing file."""
    with pytest.raises(FileNotFoundError):
        sop_engine.load_and_validate("nonexistent/path.csv")
