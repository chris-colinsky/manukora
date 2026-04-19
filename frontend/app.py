"""Streamlit executive dashboard — S&OP briefing presentation layer.

Auto-loads on open. No file uploads or button clicks required to see data.
All business logic lives in the backend; this module is strictly presentational.
"""

import os

import pandas as pd
import requests
import streamlit as st

BACKEND_URL: str = os.environ.get("BACKEND_URL", "http://localhost:8000")
GENERATE_SOP_URL: str = f"{BACKEND_URL}/api/v1/generate-sop"
DOWNLOAD_POS_URL: str = f"{BACKEND_URL}/api/v1/download-pos"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_sop_data() -> dict | None:
    """Fetch the S&OP briefing from the backend API.

    Uses st.cache_data to ensure the API is called once per session/cache expiry,
    not on every Streamlit re-render.

    Returns:
        Parsed JSON response dict, or None if the request fails.
    """
    response = requests.get(GENERATE_SOP_URL, timeout=300)
    response.raise_for_status()
    return response.json()


def fetch_po_csv() -> bytes:
    """Download the draft PO CSV from the backend.

    Returns:
        Raw CSV bytes for the st.download_button.
    """
    response = requests.get(DOWNLOAD_POS_URL, timeout=30)
    response.raise_for_status()
    return response.content


def main() -> None:
    """Render the Streamlit dashboard."""
    st.set_page_config(
        page_title="Terravita S&OP Dashboard",
        page_icon="📊",
        layout="wide",
        menu_items={"Get help": None, "Report a bug": None, "About": None},
    )
    st.markdown(
        "<style>"
        "[data-testid='stDeployButton'], "
        "[data-testid='stAppDeployButton'], "
        ".stDeployButton, "
        "button[kind='header'] "
        "{display: none !important;}"
        "</style>",
        unsafe_allow_html=True,
    )

    st.title("📊 Terravita — Weekly S&OP Briefing")
    st.caption("AI-powered Sales & Operations Planning")

    with st.spinner("Analyzing omnichannel data and generating S&OP insights..."):
        try:
            data = fetch_sop_data()
        except requests.exceptions.ConnectionError:
            st.error(
                "⚠️ Could not connect to the backend API. "
                f"Ensure the backend is running at `{BACKEND_URL}`."
            )
            return
        except requests.exceptions.HTTPError as exc:
            st.error(f"⚠️ Backend returned an error: {exc.response.status_code} — {exc}")
            return
        except Exception as exc:
            st.error(f"⚠️ Unexpected error: {exc}")
            return

    if data is None:
        st.error("No data returned from backend.")
        return

    # KPI metrics
    metrics = data.get("metrics", {})
    col1, col2 = st.columns(2)
    col1.metric(
        label="Total M4 Revenue",
        value=f"${metrics.get('total_m4_revenue', 0):,.0f}",
    )
    col2.metric(
        label="SKUs at Risk",
        value=metrics.get("skus_at_risk", 0),
        delta=None,
        delta_color="inverse",
    )

    st.divider()

    # LLM briefing
    st.subheader("Executive Briefing")
    st.markdown(data.get("llm_briefing", "_No briefing generated._"))

    st.divider()

    # At-risk SKU table
    red_flags = data.get("red_flag_data", [])
    if red_flags:
        st.subheader("🚩 SKUs at Risk")
        risk_df = pd.DataFrame(red_flags)
        risk_df["Revenue_M4"] = risk_df["Revenue_M4"].apply(lambda x: f"${x:,.2f}")
        risk_df["Effective_Months_Cover"] = risk_df["Effective_Months_Cover"].apply(
            lambda x: f"{x:.1f} mo"
        )
        risk_df["Suggested_Reorder_Qty"] = risk_df["Suggested_Reorder_Qty"].apply(
            lambda x: f"{x:,} units"
        )
        st.dataframe(risk_df, use_container_width=True, hide_index=True)
    else:
        st.success("All SKUs are within target stock cover. No red flags this week.")

    st.divider()

    # Download PO button
    st.subheader("Actions")
    try:
        po_csv = fetch_po_csv()
        st.download_button(
            label="📥 Download Draft POs (CSV)",
            data=po_csv,
            file_name="draft-purchase-orders.csv",
            mime="text/csv",
            help="Download a purchase order CSV ready for upload to Cin7.",
        )
    except Exception as exc:
        st.warning(f"Could not load PO data: {exc}")


if __name__ == "__main__":
    main()
