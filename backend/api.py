"""FastAPI application: S&OP briefing generation and PO download endpoints."""

import csv
import io
import time
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
    log.info(
        "application_started",
        env=config.ENV,
        data_file=config.DATA_FILE_PATH,
        otel_endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT or "disabled",
        langfuse_configured=bool(
            config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY
        ),
    )

    yield

    # --- Shutdown ---
    log = structlog.get_logger(__name__)
    telemetry.shutdown()
    log.info("application_shutdown")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    application = FastAPI(
        title="Manukora S&OP API",
        description=(
            "AI-powered Sales & Operations Planning briefing for Manukora. "
            "Calculates supply chain metrics with Pandas and generates "
            "executive narrative with Claude."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    application.include_router(_build_router())

    @application.get("/health", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Lightweight health check for Fly.io machine keep-alive."""
        return {"status": "ok"}

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
        start_time = time.monotonic()
        logger.info("sop_generation_started")

        calculated_df = _load_calculated_df()

        payload = sop_engine.build_llm_payload(calculated_df)

        skus_at_risk = int(calculated_df["Is_At_Risk"].sum())
        logger.info(
            "generating_llm_briefing",
            skus_at_risk=skus_at_risk,
            payload_skus=len(payload.get("all_skus", [])),
            at_risk_skus=len(payload.get("skus_at_risk", [])),
            poor_performers=len(payload.get("poor_performers", [])),
        )

        try:
            briefing = llm_service.generate_briefing(payload)
        except Exception as exc:
            logger.error("llm_call_failed", error=str(exc), exc_info=True)
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
            skus_at_risk=skus_at_risk,
        )

        elapsed = time.monotonic() - start_time
        logger.info(
            "sop_generation_completed",
            total_m4_revenue=metrics.total_m4_revenue,
            skus_at_risk=metrics.skus_at_risk,
            briefing_length=len(briefing),
            latency_seconds=round(elapsed, 2),
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
        logger.info("po_download_started")

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
        logger.info("po_download_completed", rows=len(reorder_df))

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
        logger.info("csv_loading", path=config.DATA_FILE_PATH)
        df = sop_engine.load_and_validate(config.DATA_FILE_PATH)
        logger.info("csv_loaded", rows=len(df), columns=len(df.columns))

        calculated = sop_engine.calculate(df)
        at_risk = int(calculated["Is_At_Risk"].sum())
        total_reorder = int(calculated["Suggested_Reorder_Qty"].sum())
        logger.info(
            "calculations_completed",
            skus=len(calculated),
            at_risk=at_risk,
            total_reorder_units=total_reorder,
        )
        return calculated
    except FileNotFoundError:
        logger.error("csv_not_found", path=config.DATA_FILE_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"Sales data file not found at '{config.DATA_FILE_PATH}'.",
        )
    except ValueError as exc:
        logger.error("csv_validation_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"CSV validation error: {exc}",
        )


app = create_app()

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, access_log=False)
