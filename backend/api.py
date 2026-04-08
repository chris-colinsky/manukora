"""FastAPI application: S&OP briefing generation and PO download endpoints."""

import csv
import io
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

import config
import llm_service
import sop_engine
import telemetry
from schemas import RedFlagItem, SOPMetrics, SOPResponse


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan manager for the FastAPI application.

    Handles startup and shutdown events.
    """
    # --- Startup ---
    telemetry.setup()

    log = structlog.get_logger(__name__)
    log.info("starting application", env=config.ENV)

    yield

    # --- Shutdown ---
    log = structlog.get_logger(__name__)
    log.info("shutting down application")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    application = FastAPI(
        title="Honey S&OP API",
        description=(
            "AI-powered Sales & Operations Planning briefing for Manukora. "
            "Calculates supply chain metrics with Pandas and generates "
            "executive narrative with Claude."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    application.include_router(_build_router())

    return application


def _build_router():
    """Build the API router with all S&OP endpoints.

    Returns:
        An APIRouter with generate-sop and download-pos routes.
    """
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1", tags=["S&OP"])

    @router.get(
        "/generate-sop",
        response_model=SOPResponse,
        summary="Generate weekly S&OP briefing",
    )
    def generate_sop() -> SOPResponse:
        """Read sales data, run all supply chain calculations, and generate an LLM briefing.

        No request payload required. The CSV path is configured via the DATA_FILE_PATH
        environment variable (default: data/sales-data.csv).

        Returns:
            SOPResponse containing KPI metrics, at-risk SKU data, and the LLM briefing.
        """
        logger = structlog.get_logger(__name__)
        calculated_df = _load_calculated_df()

        payload = sop_engine.build_llm_payload(calculated_df)

        logger.info(
            "generating_llm_briefing",
            skus_at_risk=int(calculated_df["Is_At_Risk"].sum()),
        )

        try:
            briefing = llm_service.generate_briefing(payload)
        except Exception as exc:
            logger.error("llm_call_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"LLM generation failed: {exc}",
            )

        at_risk_df = calculated_df[calculated_df["Is_At_Risk"]]
        red_flag_data = [
            RedFlagItem(
                SKU=row["SKU"],
                Effective_Months_Cover=round(float(row["Effective_Months_Cover"]), 2),
                Target_Months_Cover=int(row["Target_Months_Cover"]),
                Suggested_Reorder_Qty=int(row["Suggested_Reorder_Qty"]),
                Revenue_M4=round(float(row["Revenue_M4"]), 2),
            )
            for _, row in at_risk_df.iterrows()
        ]

        metrics = SOPMetrics(
            total_m4_revenue=round(float(calculated_df["Revenue_M4"].sum()), 2),
            skus_at_risk=int(calculated_df["Is_At_Risk"].sum()),
        )

        return SOPResponse(
            status="success",
            metrics=metrics,
            red_flag_data=red_flag_data,
            llm_briefing=briefing,
        )

    @router.get(
        "/download-pos",
        summary="Download draft Purchase Orders as CSV",
        response_class=StreamingResponse,
    )
    def download_pos() -> StreamingResponse:
        """Generate a PO CSV for all SKUs where Suggested_Reorder_Qty > 0.

        Returns a downloadable CSV file formatted for upload to an inventory system
        such as Cin7.  No request payload required.

        Returns:
            StreamingResponse with CSV content and attachment Content-Disposition header.
        """
        logger = structlog.get_logger(__name__)
        calculated_df = _load_calculated_df()

        reorder_df = calculated_df[calculated_df["Suggested_Reorder_Qty"] > 0][
            ["SKU", "Suggested_Reorder_Qty", "Order_Arrival_Months", "Retail_Price_USD"]
        ].copy()

        reorder_df = reorder_df.rename(
            columns={
                "Suggested_Reorder_Qty": "Order_Qty",
                "Order_Arrival_Months": "Lead_Time_Months",
            }
        )

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=reorder_df.columns.tolist())
        writer.writeheader()
        for _, row in reorder_df.iterrows():
            writer.writerow(row.to_dict())

        output.seek(0)
        logger.info("po_download", rows=len(reorder_df))

        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=draft-purchase-orders.csv"
            },
        )

    return router


def _load_calculated_df():
    """Load, validate, and calculate all S&OP metrics from the configured CSV path.

    Returns:
        A fully-calculated Pandas DataFrame.

    Raises:
        HTTPException: 500 if the CSV is missing, malformed, or validation fails.
    """
    logger = structlog.get_logger(__name__)
    try:
        df = sop_engine.load_and_validate(config.DATA_FILE_PATH)
        return sop_engine.calculate(df)
    except FileNotFoundError:
        logger.error("csv_not_found", path=config.DATA_FILE_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"Sales data file not found at '{config.DATA_FILE_PATH}'.",
        )
    except ValueError as exc:
        logger.error("csv_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"CSV validation error: {exc}",
        )


app = create_app()

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, access_log=False)
