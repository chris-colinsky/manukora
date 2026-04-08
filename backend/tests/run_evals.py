"""Standalone deepeval runner — produces rich formatted output.

Usage:
    ENV=production make test-eval

Runs the same deepeval metrics as test_evals.py but outside of pytest,
so deepeval's full console output (scores, reasons, evaluation steps)
is displayed without pytest capturing it.
"""

import json
import logging
import sys
from pathlib import Path

# Ensure the backend package root is on sys.path (same as conftest.py does for pytest).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Suppress noisy OTEL export errors (no HyperDX configured during eval runs).
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(
    logging.CRITICAL
)

import llm_service  # noqa: E402
import sop_engine  # noqa: E402
from deepeval import evaluate  # noqa: E402
from deepeval.metrics import FaithfulnessMetric, GEval  # noqa: E402
from deepeval.models import AnthropicModel  # noqa: E402
from deepeval.test_case import LLMTestCase, LLMTestCaseParams  # noqa: E402


def main() -> None:
    """Run the full deepeval evaluation suite."""
    # --- Setup ---
    print("Loading sales data and computing ground truth...")
    real_df = sop_engine.calculate(sop_engine.load_and_validate("data/sales-data.csv"))
    ground_truth_sku: str = sop_engine.get_air_freight_candidate(real_df)

    top_n = 2
    at_risk_top = real_df[real_df["Is_At_Risk"]].nlargest(top_n, "Revenue_M4")
    acceptable_skus: set[str] = {str(s) for s in at_risk_top["SKU"]}

    print(f"Ground truth air freight SKU: {ground_truth_sku}")
    print(f"Acceptable SKUs: {acceptable_skus}")
    print()

    # --- Generate briefing ---
    print("Generating S&OP briefing via LLM...")
    payload = sop_engine.build_llm_payload(real_df)
    briefing = llm_service.generate_briefing(payload)
    json_payload = json.dumps(payload, indent=2, default=str)

    print(f"Briefing length: {len(briefing)} chars")
    print()

    # --- Judge model ---
    judge = AnthropicModel(model="claude-opus-4-6", temperature=0, max_tokens=8192)

    # --- Metrics ---
    air_freight_correctness = GEval(
        name="Air Freight Correctness",
        criteria=(
            "The briefing must recommend a specific SKU for air freight. "
            "The recommended SKU should be among the highest-revenue at-risk "
            "SKUs in the data. Evaluate whether the recommendation is logically "
            "justified using revenue contribution and stock risk from the input data."
        ),
        evaluation_params=[
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        threshold=0.7,
        model=judge,
        verbose_mode=True,
    )

    briefing_completeness = GEval(
        name="Briefing Completeness",
        criteria=(
            "The S&OP briefing must contain ALL of the following sections: "
            "1) An Executive Summary of the past month's performance. "
            "2) A section highlighting what sold well vs. what sold poorly, "
            "including discussion of dead stock and discount/bundling strategy. "
            "3) A 'Red Flags' section for SKUs below target cover, with a "
            "reminder about seasonality limitations. "
            "4) Reorder recommendations for at least 3 SKUs with business "
            "reasoning referencing lead times, pipeline, and target cover. "
            "5) A note about Bioactive Blend products being new Q1 2026 "
            "launches with conservative demand modelling. "
            "6) A strategic air freight recommendation with a clearly "
            "delimited **AIR FREIGHT SKU: <name>** line."
        ),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
        threshold=0.7,
        model=judge,
        verbose_mode=True,
    )

    faithfulness = FaithfulnessMetric(
        threshold=0.7,
        model=judge,
        include_reason=True,
        verbose_mode=True,
    )

    # --- Test case ---
    test_case = LLMTestCase(
        input=json_payload,
        actual_output=briefing,
        expected_output=(
            f"The air freight recommendation should identify "
            f"'{ground_truth_sku}' as the primary candidate, "
            f"since it is the at-risk SKU with the highest Revenue_M4. "
            f"Acceptable alternatives: {acceptable_skus}."
        ),
        retrieval_context=[json_payload],
    )

    # --- Run evaluation ---
    print("=" * 70)
    print("RUNNING DEEPEVAL EVALUATION (Claude Opus as Judge)")
    print("=" * 70)
    print()

    results = evaluate(
        test_cases=[test_case],
        metrics=[air_freight_correctness, briefing_completeness, faithfulness],
    )

    sys.exit(0 if results.test_results[0].success else 1)


if __name__ == "__main__":
    main()
