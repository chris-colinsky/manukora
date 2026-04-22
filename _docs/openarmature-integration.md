# openarmature integration notes

This project uses [openarmature](https://github.com/LunarCommand/openarmature-python) to orchestrate the backend S&OP pipeline. This doc explains what the integration buys us, what it costs, and how the "calculate first, reason second" pattern maps onto openarmature's primitives.

For the pattern itself, see [`adr/0001-calculate-first-reason-second.md`](adr/0001-calculate-first-reason-second.md). For openarmature's own documentation, see the spec at [`openarmature-spec`](https://github.com/LunarCommand/openarmature-spec).

---

## What's in the graph layer

One module: [`backend/graph.py`](../backend/graph.py).

```
SOPState(file_path=...)
   │
   ▼
┌──────┐    ┌───────────┐    ┌───────────────┐    ┌──────────┐
│ load │ ──▶│ calculate │ ──▶│ build_payload │ ──▶│ briefing │ ──▶ END
└──────┘    └───────────┘    └───────────────┘    └──────────┘
```

Four async nodes, three static edges, one `END` sentinel. No conditionals, no subgraphs — the pipeline is deterministic and linear by design. Each node delegates to an existing pure function in `sop_engine` or `llm_service`, so the conversion didn't rewrite logic; it changed the orchestration shape.

## Why a graph at all for a linear pipeline

A reasonable question. Two reasons specific to this project:

**1. The pattern becomes structural, not documentary.** "Calculate first, reason second" is the thesis of this repo. With sequential function calls inside a FastAPI handler, that ordering is a convention — easy to accidentally violate in a refactor. As a compiled graph, it's load-bearing: a node that tried to call the LLM before `calculate` ran wouldn't have the `calculated_df` field on `SOPState` and would fail at node entry. The topology enforces the pattern.

**2. Boundary validation is automatic.** `SOPState` inherits `frozen=True` and `extra="forbid"` from openarmature's `State` base class. Every node's return value is re-validated against the schema at the merge boundary. A node that returns `{"typp": ...}` instead of `{"typo": ...}` raises `StateValidationError` immediately — the old sequential-call version would silently drop the key and downstream steps would produce garbage.

Neither is a decisive win for a 200-line pipeline. They become decisive when the pipeline grows to 8 or 12 nodes, which is the S&OP pipeline's plausible trajectory (more validation stages, human-in-the-loop review, multiple prompts with chained structured output, etc.).

## How the "content-agnostic" principle shows up

openarmature's charter [§3.1 Principle 2](https://github.com/LunarCommand/openarmature-spec/blob/main/docs/openarmature.md) says:

> The engine has no concept of LLMs, tools, or external systems, so validation, retry, and recovery of external inputs are node-internal concerns.

In this project, that means every LLM concern lives inside `briefing_node`:

| Concern | Location | Library |
|---|---|---|
| Prompt loading (Langfuse-first, Jinja2 fallback) | `llm_service.generate_briefing` → `prompts.load_system_prompt` / `prompts.load_user_prompt` | internal |
| Retry with exponential backoff | `llm_service._call_llm_with_retry` | tenacity |
| LLM factory (local OpenAI-compat vs production Anthropic) | `llm_service._call_local` / `llm_service._call_anthropic` | openai, anthropic |
| Trace generation to Langfuse | `llm_service.generate_briefing` | langfuse |
| OpenTelemetry span for the LLM call | `llm_service.generate_briefing` | opentelemetry |

The graph doesn't know any of that exists. It sees a node that returns `{"briefing": str, "trace": ["briefing"]}`. If LLM call semantics change — swap tenacity for a custom retry, switch to the async Anthropic SDK, add an instructor wrapper for structured output — only `llm_service.py` changes. The graph stays the same.

The same principle covers the deterministic side: `load_node` and `calc_node` don't know they're handling CSVs or DataFrames either. They call `sop_engine` functions and surface the results. Swapping the CSV input for a live database query would change `load_node`'s body; the graph topology wouldn't move.

## `SOPState` in detail

```python
class SOPState(State):
    file_path: str
    validated_df: pd.DataFrame | None = None
    calculated_df: pd.DataFrame | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    briefing: str = ""
    trace: Annotated[list[str], append] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)
```

A few notes worth sitting with:

- **`arbitrary_types_allowed=True`** is required because pandas `DataFrame` isn't a pydantic-native type. This composes with openarmature's baked-in `frozen=True` + `extra="forbid"` config via pydantic's `ConfigDict` merge — something we verified with a standalone spike before committing to the design.
- **Separate `validated_df` and `calculated_df` fields**, not a single `df`. Two reasons: (a) frozen state is per-snapshot, so keeping both lets a reader reconstruct what happened at each step; (b) the final state object can feed multiple downstream users — `/generate-sop` uses `calculated_df` for metrics + red-flag data, while the hypothetical `/diagnose` endpoint would want `validated_df` to inspect the raw-but-valid input.
- **`trace: Annotated[list[str], append]`** — the idiomatic openarmature observability pattern. Each node appends its name; the `append` reducer concatenates. At the end, `final_state.trace == ["load", "calculate", "build_payload", "briefing"]` — a compact record of what ran, useful for logs and regression tests.
- **No `tallies: dict[str, int]`** (seen in the openarmature example projects). This project already has structlog + Langfuse; a second "observability via state" channel would duplicate effort. A planned node-boundary observability hook in openarmature would unify these layers, but that's not yet available.

## Error surfaces

The graph exposes two runtime error categories the FastAPI handler catches explicitly:

| Error | When | Recoverable state? |
|---|---|---|
| `StateValidationError` | A node returns a field that isn't declared on `SOPState`, or a field with a bad type | No — the merge already failed |
| `RuntimeGraphError` (base: `NodeException`, `ReducerError`, `EdgeException`, `RoutingError`) | A node / reducer / edge function raised | Yes — `exc.recoverable_state` carries pre-failure state |

For this project, `NodeException` is the common case. `FileNotFoundError` from `sop_engine.load_and_validate` bubbles up from `load_node` → wrapped by the engine as `NodeException(cause=FileNotFoundError, recoverable_state=initial_state)` → caught in the handler.

Today we don't use `recoverable_state` — we just 500. A future iteration could inspect the partial state and offer a degraded response (e.g. "CSV load failed but here's last week's cached briefing"), which is exactly the kind of thing the `recoverable_state` field was designed to enable.

## What we're not using (and why that's fine)

openarmature has planned capabilities that this project explicitly doesn't need today:

| Feature | Why we don't need it |
|---|---|
| Conditional edges (`.add_conditional`) | No branching in the pipeline |
| Subgraphs (`.add_subgraph`) | No reusable sub-pipelines |
| Parallel/fan-out execution | openarmature enforces one outgoing edge per node; parallelism would live elsewhere if we needed it (e.g. `asyncio.gather` inside a node) |
| `merge` reducer for dict accumulation | We use the default `last_write_wins` for all scalar/DataFrame fields |
| Custom `ProjectionStrategy` | Only relevant with subgraphs |
| Batch processing / rate limiting (pipeline utilities) | Single invocation per HTTP request |
| Checkpoint/resume | Pipeline runs in under 10 seconds; resumability isn't valuable |
| Streaming | Endpoint is non-interactive (JSON response, not SSE) |

This is the content-agnostic principle paying off in reverse: the pieces we skip carry no cost. We're not importing them, not configuring them, not threading them through. An openarmature pipeline that uses half of what's available is still a valid openarmature pipeline.

## Startup and lifecycle

The graph is compiled **once** at FastAPI lifespan startup, stored in a module-level `_graph` variable, and reused per request. Compilation runs openarmature's five structural checks (`NoDeclaredEntry`, `UnreachableNode`, `DanglingEdge`, `MultipleOutgoingEdges`, `ConflictingReducers`) — if any fire, the application fails to start rather than limping along and failing on the first request.

```python
# Simplified lifespan hook
async def lifespan(app):
    global _graph
    _graph = graph.build_graph()   # compile-time checks run here
    yield
    # Shutdown: nothing to tear down; CompiledGraph is immutable and GC-clean.
```

Per-request, the handler is pure `await`:

```python
async def generate_sop() -> SOPResponse:
    final = cast(SOPState, await _graph.invoke(SOPState(file_path=...)))
    # ... build SOPResponse from final.calculated_df + final.briefing
```

No session state, no thread pools, no graph-level context objects. If FastAPI is running, the graph is ready; if a request arrives, it invokes.

## Testing

Three test files, three scopes:

- [`tests/test_sop_engine.py`](../backend/tests/test_sop_engine.py) — 23 tests on the pure Pandas functions. **Unchanged by the graph conversion** — `sop_engine.calculate` and friends are still plain functions.
- [`tests/test_graph.py`](../backend/tests/test_graph.py) — 6 tests on the graph itself: compile-time structural validity, frozen/forbid enforcement on `SOPState`, end-to-end behavior preservation (pre- vs post-conversion), error surface checks.
- [`tests/test_api.py`](../backend/tests/test_api.py) — 11 tests on the FastAPI endpoints. Uses a `TestClient` fixture wrapped in a context manager so FastAPI lifespan runs and the graph is compiled before any request.

Running `uv run pytest` from `backend/` gives all 40 tests in about a second.

## References

- openarmature charter: [`openarmature-spec/docs/openarmature.md`](https://github.com/LunarCommand/openarmature-spec/blob/main/docs/openarmature.md) — in particular §3.1 Principle 2 (content-agnostic engine) and §4.1 (graph engine module spec)
- openarmature concepts tour: lives in the openarmature example projects (`concepts.md`) — link forthcoming when the examples repo is public
- Conversion plan that produced this code: [`_plans/openarmature-conversion-plan.md`](../_plans/openarmature-conversion-plan.md)
