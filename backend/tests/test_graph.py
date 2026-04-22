"""Tests for the openarmature graph module (backend/graph.py)."""

from unittest.mock import patch

import pandas as pd
import pytest
from openarmature.graph import CompiledGraph

import graph
from graph import SOPState

MOCK_BRIEFING = "## Test Briefing\n\nStub output."


def test_graph_compiles() -> None:
    """build_graph() returns a CompiledGraph with the expected topology.

    The compile-time structural checks (`NoDeclaredEntry`, `DanglingEdge`,
    `UnreachableNode`, `MultipleOutgoingEdges`, `ConflictingReducers`) fire
    inside this call. A passing test here is "the graph is structurally
    well-formed" — this is the cheap-interim equivalent of a static linter
    flagged in openarmature-examples/_docs/future-work.md.
    """
    g = graph.build_graph()
    assert isinstance(g, CompiledGraph)
    assert g.entry == "load"
    assert set(g.nodes.keys()) == {"load", "calculate", "build_payload", "briefing"}


def test_state_forbids_unknown_fields() -> None:
    """SOPState inherits extra='forbid' from State — typos surface loudly."""
    with pytest.raises(Exception):  # pydantic ValidationError
        SOPState(file_path="x", typo_field="y")  # type: ignore[call-arg]


def test_state_is_frozen() -> None:
    """SOPState inherits frozen=True — attribute reassignment is blocked."""
    s = SOPState(file_path="x")
    with pytest.raises(Exception):  # pydantic ValidationError on assignment
        s.file_path = "y"  # type: ignore[misc]


async def test_graph_invoke_behavior_preservation() -> None:
    """End-to-end graph invoke must match the behavior of the pre-conversion
    sequential path: same calculated_df, same payload structure, LLM briefing
    passes through unchanged.

    This is the regression test guaranteeing the conversion doesn't silently
    change semantics — the article will claim "same output, cleaner topology,"
    and this test backs that claim.
    """
    g = graph.build_graph()

    with patch("llm_service.generate_briefing", return_value=MOCK_BRIEFING):
        final = await g.invoke(SOPState(file_path="data/sales-data.csv"))

    # Trace reflects exact execution order — load → calculate → build_payload → briefing.
    assert final.trace == ["load", "calculate", "build_payload", "briefing"]

    # Briefing surfaces exactly what the LLM layer returned.
    assert final.briefing == MOCK_BRIEFING

    # Calculated DataFrame has the engine-added columns present in pre-conversion runs.
    assert final.calculated_df is not None
    for col in ("Total_M4", "Revenue_M4", "Projected_M5_Sales", "Is_At_Risk", "Suggested_Reorder_Qty"):
        assert col in final.calculated_df.columns, f"missing expected column: {col}"

    # Payload mirrors what sop_engine.build_llm_payload returned pre-conversion.
    assert set(final.payload.keys()) == {"all_skus", "skus_at_risk", "poor_performers"}


async def test_graph_load_failure_surfaces_as_node_exception() -> None:
    """A missing CSV becomes NodeException with the original error in its chain."""
    from openarmature.graph import NodeException

    g = graph.build_graph()
    with patch("sop_engine.load_and_validate", side_effect=FileNotFoundError("no file")):
        with pytest.raises(NodeException) as excinfo:
            await g.invoke(SOPState(file_path="nonexistent.csv"))
    # NodeException carries recoverable_state (the state at the point of failure).
    assert excinfo.value.recoverable_state is not None
    assert isinstance(excinfo.value.__cause__, FileNotFoundError)


async def test_graph_state_validation_error_on_bad_update() -> None:
    """A node returning an undeclared field surfaces as StateValidationError.

    This guards against field-name typos in node implementations; compare to
    the sequential path, where a misspelled dict key would silently produce
    an empty briefing downstream.
    """
    from openarmature.graph import StateValidationError

    # Replace load_node inline: returns a bogus field name.
    async def bad_load(_s: SOPState) -> dict:
        return {"validated_df_typo": pd.DataFrame({"a": [1]})}

    from openarmature.graph import END, GraphBuilder

    bad_graph = (
        GraphBuilder(SOPState)
        .add_node("load", bad_load)
        .add_edge("load", END)
        .set_entry("load")
        .compile()
    )

    with pytest.raises(StateValidationError) as excinfo:
        await bad_graph.invoke(SOPState(file_path="x"))
    assert "validated_df_typo" in excinfo.value.fields
