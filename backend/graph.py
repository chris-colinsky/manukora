"""S&OP briefing pipeline as an openarmature graph.

The pipeline is a four-step linear transformation from a CSV path to an
executive markdown briefing. Each step is a node in an openarmature graph;
each node delegates to an existing pure function in `sop_engine` or
`llm_service`, which keeps the calculation/LLM logic testable in isolation
and the graph a thin orchestration layer.

Shape::

    SOPState(file_path=...)
       │
       ▼
    ┌──────────┐     ┌───────────┐     ┌───────────────┐     ┌──────────┐
    │   load   │ ──▶ │ calculate │ ──▶ │ build_payload │ ──▶ │ briefing │ ──▶ END
    └──────────┘     └───────────┘     └───────────────┘     └──────────┘
         │                 │                  │                   │
         │                 │                  │                   │
      validated_df    calculated_df        payload             briefing
                                                                  ▲
                                         ┌────────────────────────┘
                                         │  LLM call + prompt load + retry +
                                         │  Langfuse tracing are all INSIDE
                                         │  briefing_node, delegated to
                                         │  llm_service.generate_briefing.
                                         │  The graph doesn't know LLMs exist
                                         │  (openarmature charter §3.1 P2).
                                         └

Why a graph at all for a linear pipeline? Two reasons specific to this
project:

  1. **Explicit topology.** The "calculate first, reason second" pattern
     is easier to see — and harder to accidentally violate — when the
     pipeline is declared as a graph than when it's sequential function
     calls inside a FastAPI handler. A future reader (or linter) sees at
     a glance that the LLM call is the terminal step and that every
     prior node is deterministic.

  2. **Boundary enforcement.** openarmature's `State` is frozen and
     `extra="forbid"`; every node's output is validated at the merge
     boundary. A node that returns a misspelled field name fails loudly
     with `StateValidationError` instead of silently dropping data
     that would later produce an empty briefing.

This module is intentionally small. If it grows beyond ~150 lines, the
right move is to extract node bodies into their own modules, not to
inline logic here. The graph's job is orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

import pandas as pd
import structlog
from openarmature.graph import END, CompiledGraph, GraphBuilder, State, append
from pydantic import ConfigDict, Field

import llm_service
import sop_engine

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------------
# State schema
# ----------------------------------------------------------------------------
# The single object that flows through the graph. Every node reads from it
# and returns a partial update; the engine merges via per-field reducers
# (`last_write_wins` is the default; `trace` uses `append` to accumulate
# node names in the order they ran).
#
# Why separate `validated_df` and `calculated_df` instead of mutating one
# `df` field? Three reasons:
#   - Frozen state: each intermediate snapshot is its own immutable value.
#   - Debuggability: the pre-calculation DataFrame is still on `final_state`
#     if a calculation step misbehaves in production.
#   - The `trace` field names which nodes produced which columns; keeping
#     the inputs around makes that trace more useful than the output alone.
#
# `arbitrary_types_allowed` is required for pandas DataFrames (they aren't
# a pydantic-native type). It composes with the `frozen=True` and
# `extra="forbid"` settings that `State` bakes into its base class —
# pydantic merges parent and subclass `model_config` in the expected way.

class SOPState(State):
    """State flowing through the S&OP graph.

    Attributes:
        file_path: Path to the sales CSV. Set by the caller; read by `load_node`.
        validated_df: Row-validated DataFrame produced by `load_node`.
        calculated_df: DataFrame with all S&OP calculations added. Produced
            by `calc_node` and consumed by both `payload_node` and the
            FastAPI handler (for at-risk rows, metrics, and PO generation).
        payload: Dictionary shaped for the LLM prompt. Produced by
            `payload_node` and consumed by `briefing_node`.
        briefing: Final markdown string produced by `briefing_node`.
        trace: Append-only list of node names in execution order.
            Useful for logging, testing, and post-hoc inspection.
    """

    file_path: str
    validated_df: pd.DataFrame | None = None
    calculated_df: pd.DataFrame | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    briefing: str = ""
    trace: Annotated[list[str], append] = Field(default_factory=list)

    # pandas DataFrame isn't a pydantic-native type. `arbitrary_types_allowed`
    # tells pydantic to accept it; the frozen + extra-forbid settings baked
    # into `State` still apply.
    model_config = ConfigDict(arbitrary_types_allowed=True)


# ----------------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------------
# Each node is `async def(state) -> Mapping[str, Any]`. The body delegates
# to an existing pure function in `sop_engine` or `llm_service` — the
# graph is orchestration, not business logic.
#
# Note on async + synchronous delegates. The underlying functions
# (`sop_engine.load_and_validate`, `llm_service.generate_briefing`, etc.)
# are synchronous. Calling them from an async node blocks the event loop
# for the duration of each call — acceptable here because (a) the Pandas
# work is sub-second on a dozen SKUs, and (b) the FastAPI handler serves
# one request at a time in typical use. For higher concurrency, wrap each
# call in `asyncio.to_thread(...)`; for LLM calls specifically, migrate
# to the async SDK (AsyncAnthropic / AsyncOpenAI). That's a node-internal
# change; the graph doesn't have to know.


async def load_node(s: SOPState) -> Mapping[str, Any]:
    """Load and row-validate the sales CSV."""
    logger.info("node_started", node="load", path=s.file_path)
    df = sop_engine.load_and_validate(s.file_path)
    logger.info("node_completed", node="load", rows=len(df))
    return {"validated_df": df, "trace": ["load"]}


async def calc_node(s: SOPState) -> Mapping[str, Any]:
    """Run the deterministic S&OP calculations."""
    assert s.validated_df is not None, "validated_df should be set by load_node"
    logger.info("node_started", node="calculate")
    calculated = sop_engine.calculate(s.validated_df)
    at_risk = int(calculated["Is_At_Risk"].sum())
    logger.info("node_completed", node="calculate", rows=len(calculated), at_risk=at_risk)
    return {"calculated_df": calculated, "trace": ["calculate"]}


async def payload_node(s: SOPState) -> Mapping[str, Any]:
    """Shape the calculated DataFrame into the LLM prompt payload.

    Deliberately excludes the `Air_Freight_Candidate` value — that's
    ground truth for the eval suite, not input for the LLM.
    """
    assert s.calculated_df is not None, "calculated_df should be set by calc_node"
    logger.info("node_started", node="build_payload")
    payload = sop_engine.build_llm_payload(s.calculated_df)
    logger.info(
        "node_completed",
        node="build_payload",
        all_skus=len(payload.get("all_skus", [])),
        at_risk=len(payload.get("skus_at_risk", [])),
        poor_performers=len(payload.get("poor_performers", [])),
    )
    return {"payload": payload, "trace": ["build_payload"]}


async def briefing_node(s: SOPState) -> Mapping[str, Any]:
    """Generate the executive briefing from the LLM.

    All LLM concerns — prompt loading (Langfuse-first with local Jinja2
    fallback), tenacity retry on transient failures, Langfuse tracing
    of the generation, factory selection between local vLLM/LM Studio
    and production Anthropic — live inside `llm_service.generate_briefing`.
    The graph doesn't know any of that exists. That's the
    content-agnostic principle (openarmature charter §3.1 Principle 2)
    doing its job: LLM validation, retry, and recovery are node-internal
    concerns.
    """
    logger.info("node_started", node="briefing")
    briefing = llm_service.generate_briefing(s.payload)
    logger.info("node_completed", node="briefing", briefing_length=len(briefing))
    return {"briefing": briefing, "trace": ["briefing"]}


# ----------------------------------------------------------------------------
# Graph construction
# ----------------------------------------------------------------------------
# Linear pipeline: load → calculate → build_payload → briefing → END.
# No conditionals, no subgraphs. This is the simplest shape openarmature
# supports and it's the right shape for this project — the computation
# is deterministic and known up-front.
#
# `.compile()` runs structural checks and returns an immutable
# `CompiledGraph`. Any graph-shape problem (dangling edge, unreachable
# node, etc.) surfaces HERE, at module import time — not at request time.


def build_graph() -> CompiledGraph:
    """Build and compile the S&OP graph.

    Returns:
        Compiled graph ready to invoke. Construct once at startup and reuse.
    """
    return (
        GraphBuilder(SOPState)
        .add_node("load", load_node)
        .add_node("calculate", calc_node)
        .add_node("build_payload", payload_node)
        .add_node("briefing", briefing_node)
        .add_edge("load", "calculate")
        .add_edge("calculate", "build_payload")
        .add_edge("build_payload", "briefing")
        .add_edge("briefing", END)
        .set_entry("load")
        .compile()
    )
