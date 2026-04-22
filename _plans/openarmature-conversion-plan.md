# Plan: openarmature Conversion

## Summary

Convert the current backend from its sequential function-call pipeline to an
[openarmature](https://github.com/LunarCommand/openarmature-python) graph.
The backend logic (Pandas calculations, Pydantic validation, LLM call with
Langfuse + tenacity) stays intact; what changes is that the orchestration
of those steps becomes a compiled `CompiledGraph` instead of imperative
function calls inside a FastAPI handler.

**Article context.** This project is the generic rebrand of the project
referenced as "Manukora S&OP" in the openarmature charter §2.1 evidence
table. The planned article about the "calculate first, reason second"
pattern will accompany this conversion and will be the **first public
mention of openarmature**. The conversion therefore needs to read as a
teachable reference — code clarity and narrative matter alongside
correctness.

## Requirements / Design References

No formal `_reqs/` entry for this conversion. The plan is distilled from:

- `openarmature-spec/docs/openarmature.md` — charter (especially §3.1
  Principle 2 *content-agnostic engine* and §4.1 *Graph Engine*)
- `openarmature-examples/_docs/concepts.md` — conceptual tour of the
  graph primitives
- `openarmature-examples/_docs/rough-edges.md` and `future-work.md` —
  known friction points and planned capabilities
- Design conversation on 2026-04-20 that produced the assessment below

## Clarifications & Decisions

- **Scope**: backend only. Frontend (Streamlit) stays unchanged — it
  consumes the same HTTP API. Graph is invoked from inside FastAPI
  handlers, not replacing FastAPI.
- **Existing behaviors preserved**: the LLM factory (local vs
  production), tenacity retry, Langfuse tracing, OpenTelemetry spans,
  and structlog logging all stay. Per openarmature charter §3.1
  Principle 2, these are node-internal concerns and the graph engine
  doesn't touch them.
- **Two HTTP endpoints** (`/generate-sop` and `/download-pos`) share
  the load + calculate half of the pipeline. Resolution: start by
  keeping `sop_engine.calculate` callable directly from
  `/download-pos`, reusing only the pure-function layer. Lift to a
  shared compiled subgraph only if a third endpoint needs the same
  prefix.
- **Test suite**: preserve without refactor where possible.
  `test_sop_engine.py` and `test_evals.py` should not need changes
  because the units under test (calc functions, LLM payload + output)
  aren't moving. `test_api.py` needs minor updates for async
  invocation.
- **Verbose comments in graph code**: the article will surface this
  code. Aim for teaching-quality annotation in `backend/graph.py`,
  similar to `openarmature-examples/01-linear-pipeline/main.py`.

## Fit Assessment

Every stage maps 1:1 onto openarmature primitives with no friction:

| Current | openarmature primitive | Notes |
|---|---|---|
| `sop_engine.load_and_validate` | Node with partial update `{"validated_df": df}` | Pure function, stays identical |
| `sop_engine.calculate` | Node with partial update `{"calculated_df": df}` | Pure function, stays identical |
| `sop_engine.build_llm_payload` | Node with partial update `{"payload": ...}` | Pure function, stays identical |
| `llm_service.generate_briefing` | Node with partial update `{"briefing": text}` | Prompt loading + tenacity retry + Langfuse tracing stay inside the node per §3.1 P2 |
| Pydantic models everywhere | `State` subclass | Already idiomatic; just inherit from `openarmature.graph.State` instead of `BaseModel` |
| Linear flow, no branching | 4 static edges | One-outgoing-edge rule is a natural fit |
| FastAPI handler as orchestrator | `await graph.invoke(state)` from inside handler | Graph embeds cleanly in an async handler |

**Nothing in the project uses a capability openarmature lacks.**

| openarmature planned-but-not-shipped feature | Required? |
|---|---|
| Batch processing (§4.2) | No — single invocation per request |
| Rate limiting (§4.2) | No — single LLM call |
| Checkpoint/resume (§4.2) | No — fast pipeline, no resumability needed |
| Streaming | No — non-interactive endpoint |
| HITL / interrupts | No |
| Prompt management (§4.5) | Project has its own (Langfuse + Jinja2 fallback); stays inside the briefing node |
| Observability interfaces (§4.6) | Project has its own (Langfuse + OTEL); stays inside nodes |

## Phases

### Phase 0 — DataFrame-in-State spike (~30 min)

**Before committing to the design**, verify that a pandas DataFrame can
live inside an `openarmature.graph.State` subclass. `State` has
`frozen=True` + `extra="forbid"` baked in; a subclass needs
`arbitrary_types_allowed=True` for DataFrame fields. Pydantic merges
parent and subclass `ConfigDict`, but this combo isn't exercised by
existing openarmature tests.

```python
from openarmature.graph import State
from pydantic import ConfigDict, Field
import pandas as pd

class SpikeState(State):
    df: pd.DataFrame | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

s = SpikeState(df=pd.DataFrame({"a": [1, 2]}))
# Verify: construction works; re-validation after a node returns a new
# DataFrame works; frozen semantics behave as expected (model attr
# reassignment blocked, DataFrame contents still mutable — which is
# fine because reducers produce new refs).
```

If the spike fails or has unacceptable re-validation cost, fall back to
storing `file_path` in state and treating DataFrame as a node-local
value passed via a different channel (e.g. module-level cache keyed by
invocation id, or a tiny path-through of only the columns the LLM
payload needs).

### Phase 1 — Graph module (~2–3 hours)

New file: `backend/graph.py`.

Contents:

- `SOPState(State)` — typed state schema with DataFrame fields plus
  `trace: Annotated[list[str], append]` and `tallies: Annotated[dict[str, int], merge]`
  (optional but low-cost and makes internal observability visible
  without a hook)
- Four async node functions (`load_node`, `calc_node`, `payload_node`,
  `briefing_node`) — each delegates to the existing `sop_engine` /
  `llm_service` functions; each returns a partial update
- `build_graph() -> CompiledGraph` — static construction, entry =
  `load_node`, terminal = `briefing_node → END`

Verbose teaching comments throughout, matching the style of
`openarmature-examples/01-linear-pipeline/main.py`.

### Phase 2 — Wire into FastAPI (~1 hour)

Modify `backend/api.py`:

- `/generate-sop` handler becomes:
  ```python
  state = SOPState(file_path=config.DATA_FILE_PATH)
  final = cast(SOPState, await graph.invoke(state))
  # Build SOPResponse from final.calculated_df + final.briefing
  ```
- `/download-pos` continues to call `sop_engine.load_and_validate` +
  `sop_engine.calculate` directly — no graph needed; the two static
  functions are still public and pure.

Startup in the lifespan manager: call `build_graph()` once and store it
on `app.state.graph` (or module-level) to avoid rebuilding per request.

### Phase 3 — Test delta (~1 hour)

- `test_sop_engine.py`: expected unchanged — `calculate()` is untouched.
- `test_evals.py`: expected unchanged — the LLM output and the prompt
  are unchanged.
- `test_api.py`: update to use `async def` + async test client, or
  drive the graph synchronously via `asyncio.run` in a fixture. Minor.
- New: one or two unit tests in `backend/tests/test_graph.py`:
  - `test_graph_compiles_without_errors` (the "cheap interim"
    described in `openarmature-examples/_docs/future-work.md` for the
    static-linter idea)
  - `test_graph_invoke_produces_same_briefing` — regression test
    comparing the old sequential path's output to the graph's output,
    so we can prove the conversion is behavior-preserving

### Phase 4 — Narrative polish (~1 hour)

Given the article context:

- Re-read `backend/graph.py` and make sure the teaching comments land
  for a first-time reader.
- Update `CLAUDE.md` to mention that backend orchestration uses
  openarmature.
- Update `README.md` architecture section to show the graph topology
  explicitly (mermaid or ASCII).
- Add a short `_docs/openarmature-integration.md` that maps
  charter-§3.1-Principle-2 to the actual shape of `backend/graph.py`
  (useful reference for the article).

## Gotchas to verify before committing

1. **DataFrame in State (Phase 0)** — covered above.
2. **Re-validation cost on DataFrame-bearing state** — each merge
   re-validates the full State via pydantic. For 12 SKUs this is
   trivial; worth measuring if DataFrames grow. Flag if measurably
   slow.
3. **Async FastAPI handlers** — the existing `/generate-sop` handler
   is `def` (sync). `graph.invoke` is async, so the handler becomes
   `async def`. Minor but needs to be intentional. FastAPI is fine
   with either; tests need to handle async.
4. **Observability composition** — Langfuse wrapping of the LLM call
   currently happens inside `llm_service.generate_briefing`. In the
   graph world, that call is inside `briefing_node`. Langfuse spans
   will still attach to the node's execution but won't automatically
   know the node's name or graph context. Acceptable for v1; the
   future-work **node-boundary observability hook** would close this
   gap later.

## Multi-endpoint strategy (future-proofing note)

Today: `/generate-sop` uses the graph; `/download-pos` calls the two
pure calc functions directly. This works because
`sop_engine.{load_and_validate,calculate}` are still public, stateless,
and cheap.

If a third endpoint arrives that also needs the calculated DataFrame,
revisit: the right move then is probably to compile a "calc-only"
subgraph (`load → calc → END` over `SOPState`) and use
`builder.add_subgraph` from both outer graphs that need it. That keeps
the computation shape defined in one place while still letting each
endpoint do its own post-calc work. Not worth doing today.

## What this conversion proves (dogfood value)

Four things worth surfacing in the article, and worth noting in
`openarmature-examples/_docs/rough-edges.md` or `future-work.md` if any
turn up friction:

1. **DataFrame-in-State ergonomics** — does pydantic's
   arbitrary-types-allowed compose with openarmature's frozen+forbid
   config in practice?
2. **Content-agnostic principle in the wild** — does the existing LLM
   tooling (tenacity, Langfuse, prompt loader) compose cleanly inside
   a node, or does the content-agnostic principle (§3.1 P2) create
   friction at the boundary?
3. **FastAPI embedding pattern** — is "compile once at lifespan startup,
   await from handler" the right affordance, or is something
   ergonomic missing?
4. **Real-world retry placement** — the existing tenacity wrapper
   lives at the SDK boundary. Does it stay there, or is there value in
   lifting it to a graph-level retry node with a conditional edge?
   Answers the question the future-work "LLM response validation +
   retry helpers" entry flagged.

## Out of scope

- **Frontend changes.** Streamlit consumes HTTP, not the graph
  directly; no changes.
- **Charter updates.** The charter §2.1 still cites "Manukora S&OP."
  Whether to note the generic rebrand in the charter is a separate
  decision, not part of this conversion.
- **openarmature-spec proposals.** Nothing about this conversion
  requires new spec behavior. If a limitation surfaces during the work
  that needs a proposal, it goes into `openarmature-examples/_docs/future-work.md`
  first and only becomes a proposal if the pattern matters beyond this
  one project.
- **Publishing openarmature to PyPI.** The examples repo currently
  consumes it via editable path dep; this project would do the same
  until openarmature ships a release.

## Open questions

1. **Article positioning.** Does the article showcase openarmature
   explicitly (named, cited, linked), or does it present the pattern
   first and mention openarmature as "the framework we used to express
   it"? The framing affects how much openarmature-specific
   explanation lands in `_docs/openarmature-integration.md` vs the
   article itself.
2. **Charter evidence line.** Does the charter §2.1 Manukora S&OP
   entry get updated to reference the generic public reference
   implementation, or stay as-is? (Not blocking.)
3. **Release sequencing with article.** The article and the converted
   repo should land together. Does openarmature need a tagged release
   first so the article can point to a stable version, or is an
   editable path dep from a pinned commit acceptable for v0?
