"""S&OP calculation engine — all supply chain math happens here, before any LLM call.

Implements the Calculate First, Reason Second architecture (ADR 0001).
All formulas are deterministic and fully unit-testable.
"""

import pandas as pd

from schemas import SalesRow

STAGNANT_COVER_VALUE: int = 999
POOR_PERFORMER_COVER_THRESHOLD: float = 6.0
BIOSYNERGY_KEYWORD: str = "BioSynergy"


def load_and_validate(file_path: str) -> pd.DataFrame:
    """Load a CSV file and validate every row against the SalesRow Pydantic schema.

    Args:
        file_path: Path to the sales data CSV file.

    Returns:
        A validated Pandas DataFrame.

    Raises:
        ValueError: If any column is missing or a row fails type validation.
    """
    df = pd.read_csv(file_path)

    required_columns = set(SalesRow.model_fields.keys())
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # Validate each row; collect errors to surface a single descriptive message.
    errors: list[str] = []
    for idx, row in df.iterrows():
        try:
            SalesRow(**{str(k): v for k, v in row.to_dict().items()})
        except Exception as exc:
            errors.append(f"Row {idx} ({row.get('SKU', '?')}): {exc}")
    if errors:
        raise ValueError("CSV validation failed:\n" + "\n".join(errors))

    return df


def calculate(df: pd.DataFrame) -> pd.DataFrame:
    """Run all S&OP calculations on the validated DataFrame.

    Adds the following columns to a copy of the input DataFrame:

    - Total_M1 through Total_M4: Omnichannel (Shopify + Amazon) unit sales per month.
    - Revenue_M4: Total_M4 * Retail_Price_USD.
    - MoM_Growth_Avg: Average month-over-month growth rate across the last 3 periods.
    - Projected_M5_Sales: Forecast for next month (capped at ≥0; BioSynergy = M4).
    - Current_Months_Cover: Stock_On_Hand / Projected_M5_Sales.
    - Effective_Months_Cover: (Stock_On_Hand + Units_On_Order) / Projected_M5_Sales.
    - Is_At_Risk: True when Effective_Months_Cover < Target_Months_Cover.
    - Total_Pipeline_Needed: Units required to satisfy lead time + target safety stock.
    - Suggested_Reorder_Qty: MAX(0, Total_Pipeline_Needed - current pipeline).

    Division-by-zero guard: when Projected_M5_Sales == 0, cover values are set to
    STAGNANT_COVER_VALUE (999) and Is_At_Risk is False.

    Args:
        df: A validated DataFrame matching the SalesRow schema.

    Returns:
        A new DataFrame with all calculated columns appended.
    """
    out = df.copy()

    # A. Omni-channel totals
    for m in range(1, 5):
        out[f"Total_M{m}"] = out[f"Shopify_Units_M{m}"] + out[f"Amazon_Units_M{m}"]
    out["Revenue_M4"] = out["Total_M4"] * out["Retail_Price_USD"]

    # B. Growth & projections
    mom_rates: list[pd.Series] = []
    for m in range(1, 4):
        prev = out[f"Total_M{m}"].replace(0, pd.NA)
        curr = out[f"Total_M{m + 1}"]
        mom_rates.append((curr - prev) / prev)
    out["MoM_Growth_Avg"] = pd.concat(mom_rates, axis=1).mean(axis=1)

    # BioSynergy exception: use M4 as steady-state baseline to avoid over-forecasting
    # initial launch spike.
    projected = out["Total_M4"] * (1 + out["MoM_Growth_Avg"])
    # fillna(0) handles SKUs where all historical sales are 0 (NaN from MoM calc).
    projected = projected.clip(lower=0).fillna(0)
    is_biosynergy = out["SKU"].str.contains(BIOSYNERGY_KEYWORD, na=False)
    out["Projected_M5_Sales"] = projected.where(~is_biosynergy, out["Total_M4"])

    # C. Stock cover
    safe_projected = out["Projected_M5_Sales"].replace(0, pd.NA)
    current_cover = out["Stock_On_Hand"] / safe_projected
    effective_cover = (out["Stock_On_Hand"] + out["Units_On_Order"]) / safe_projected

    out["Current_Months_Cover"] = current_cover.fillna(STAGNANT_COVER_VALUE)
    out["Effective_Months_Cover"] = effective_cover.fillna(STAGNANT_COVER_VALUE)

    # Is_At_Risk is False for stagnant items (Projected_M5 == 0)
    stagnant_mask = out["Projected_M5_Sales"] == 0
    out["Is_At_Risk"] = (
        out["Effective_Months_Cover"] < out["Target_Months_Cover"]
    ) & ~stagnant_mask

    # D. Reorder quantities — only for at-risk SKUs.
    # SKUs with sufficient effective cover (Is_At_Risk == False) should not
    # generate purchase orders; reordering healthy stock wastes cash flow.
    out["Total_Pipeline_Needed"] = (
        out["Target_Months_Cover"] + out["Order_Arrival_Months"]
    ) * out["Projected_M5_Sales"]
    current_pipeline = out["Stock_On_Hand"] + out["Units_On_Order"]
    raw_reorder = (out["Total_Pipeline_Needed"] - current_pipeline).clip(lower=0)
    out["Suggested_Reorder_Qty"] = raw_reorder.where(out["Is_At_Risk"], 0).astype(int)

    return out


def get_air_freight_candidate(df: pd.DataFrame) -> str:
    """Return the SKU most critical for air freight consideration.

    This is the at-risk SKU with the highest Revenue_M4.  It is intentionally
    NOT included in the LLM JSON payload — it is used only as ground truth in
    the deepeval test suite.

    Args:
        df: A DataFrame that has already been passed through calculate().

    Returns:
        The SKU string of the air freight candidate, or an empty string if no
        SKUs are at risk.
    """
    at_risk = df[df["Is_At_Risk"]]
    if at_risk.empty:
        return ""
    return str(at_risk.loc[at_risk["Revenue_M4"].idxmax(), "SKU"])


def get_poor_performers(df: pd.DataFrame) -> pd.DataFrame:
    """Filter for SKUs with declining sales and excess inventory.

    Args:
        df: A DataFrame that has already been passed through calculate().

    Returns:
        Subset of rows where MoM_Growth_Avg < 0 and Effective_Months_Cover > 6.
    """
    return df[
        (df["MoM_Growth_Avg"] < 0)
        & (df["Effective_Months_Cover"] > POOR_PERFORMER_COVER_THRESHOLD)
    ]


def build_llm_payload(df: pd.DataFrame) -> dict:
    """Construct the JSON payload that will be passed to the LLM.

    Deliberately excludes Air_Freight_Candidate (ground truth for deepeval).

    Args:
        df: A DataFrame that has already been passed through calculate().

    Returns:
        A dictionary ready to be serialised as the LLM user-prompt payload.
    """
    poor_performers = get_poor_performers(df)

    payload_columns = [
        "SKU",
        "Total_M1",
        "Total_M2",
        "Total_M3",
        "Total_M4",
        "Revenue_M4",
        "MoM_Growth_Avg",
        "Projected_M5_Sales",
        "Stock_On_Hand",
        "Units_On_Order",
        "Order_Arrival_Months",
        "Target_Months_Cover",
        "Current_Months_Cover",
        "Effective_Months_Cover",
        "Is_At_Risk",
        "Suggested_Reorder_Qty",
    ]

    return {
        "all_skus": df[payload_columns].to_dict(orient="records"),
        "skus_at_risk": df[df["Is_At_Risk"]][payload_columns].to_dict(orient="records"),
        "poor_performers": (
            poor_performers[payload_columns].to_dict(orient="records")
            if not poor_performers.empty
            else []
        ),
    }
