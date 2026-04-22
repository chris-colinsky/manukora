"""FastAPI application: S&OP briefing generation and PO download endpoints.

/generate-sop is orchestrated through an openarmature graph compiled at
application startup. /download-pos bypasses the graph and calls the pure
sop_engine functions directly — the LLM-free half of the pipeline is just
Python and doesn't need a graph to stay readable.
"""

import csv
import io
import time
from contextlib import asynccontextmanager
from typing import cast

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openarmature.graph import CompiledGraph, RuntimeGraphError, StateValidationError

import config
import graph
import sop_engine
import telemetry
from graph import SOPState
from schemas import RedFlagItem, SOPMetrics, SOPResponse


# Compiled once at lifespan startup and reused per request. Module-level
# (rather than app.state) keeps the handlers closure-simple — they read
# `_graph` directly without a `Request` injection.
_graph: CompiledGraph | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan manager for the FastAPI application.

    Handles startup and shutdown events.
    """
    # --- Startup ---
    telemetry.setup()

    # Compile the S&OP graph once at startup. Any structural problem
    # (dangling edge, unreachable node, reducer conflict) surfaces here
    # rather than on the first request.
    global _graph
    _graph = graph.build_graph()

    log = structlog.get_logger(__name__)
    log.info(
        "application_started",
        env=config.ENV,
        data_file=config.DATA_FILE_PATH,
        otel_endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT or "disabled",
        langfuse_configured=bool(
            config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY
        ),
        graph_compiled=True,
        graph_entry=_graph.entry,
        graph_nodes=list(_graph.nodes.keys()),
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
        title="Terravita S&OP API",
        description=(
            "AI-powered Sales & Operations Planning briefing for Terravita. "
            "Calculates supply chain metrics with Pandas and generates "
            "executive narrative with an LLM."
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
    async def generate_sop() -> SOPResponse:
        """Read sales data, run all supply chain calculations, and generate an LLM briefing.

        Orchestrated through the openarmature graph compiled at application
        startup (`load → calculate → build_payload → briefing → END`).
        Each node contributes a typed partial update; the engine validates
        `SOPState` at every merge boundary. LLM concerns (prompt loading,
        retry, tracing) live inside `briefing_node` — the graph itself is
        LLM-agnostic per openarmature charter §3.1 Principle 2.

        No request payload required. The CSV path is configured via the
        DATA_FILE_PATH environment variable (default: data/sales-data.csv).

        Returns:
            SOPResponse containing KPI metrics, at-risk SKU data, and the LLM briefing.
        """
        logger = structlog.get_logger(__name__)
        start_time = time.monotonic()
        logger.info("sop_generation_started")

        assert _graph is not None, "graph not compiled; lifespan startup didn't run"

        try:
            final = cast(
                SOPState,
                await _graph.invoke(SOPState(file_path=config.DATA_FILE_PATH)),
            )
        except StateValidationError as exc:
            # Merged state failed schema validation — bad field name or type
            # returned by a node. Not recoverable at the graph level.
            logger.error("graph_state_validation_failed", fields=exc.fields, error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"State validation error: {exc}",
            )
        except RuntimeGraphError as exc:
            # NodeException / EdgeException / ReducerError / RoutingError all
            # carry `recoverable_state` if we wanted to surface a partial
            # result. For now, log and return 500.
            logger.error(
                "graph_execution_failed",
                error_class=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Pipeline failure ({type(exc).__name__}): {exc}",
            )

        # Graph post-condition: calculated_df is set by calc_node, briefing
        # is set by briefing_node. Both are reachable from entry via static
        # edges, so if we got here, both ran successfully.
        calculated_df = final.calculated_df
        assert calculated_df is not None, "calc_node should have set calculated_df"

        skus_at_risk = int(calculated_df["Is_At_Risk"].sum())
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
            briefing_length=len(final.briefing),
            latency_seconds=round(elapsed, 2),
            trace=final.trace,
        )

        return SOPResponse(
            status="success",
            metrics=metrics,
            red_flag_data=red_flag_data,
            llm_briefing=final.briefing,
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
