# ADR 0006: deepeval for LLM Output Validation

**Status:** Accepted  
**Date:** 2026-04-06

## Context

The S&OP briefing asks the LLM to identify the single highest-priority SKU for air freight based on revenue contribution and stock risk. This is a reasoning task, not a retrieval task — the correct answer is deterministically calculable from the Pandas engine (`Air_Freight_Candidate`), but the LLM must arrive at it through its own analysis of the payload.

We need a way to assert that the LLM is reasoning correctly, not just producing plausible-sounding text.

## Decision

We use `deepeval` to implement an evaluation test in `backend/tests/test_evals.py`. The test:

1. Runs the full pipeline to generate a real LLM briefing.
2. Parses the briefing markdown to extract the SKU named after `**AIR FREIGHT SKU:**`.
3. Asserts the extracted SKU matches `Air_Freight_Candidate` as calculated by `sop_engine.py`.

`Air_Freight_Candidate` is deliberately **excluded from the JSON payload sent to the LLM** — it is ground truth for the test only. The LLM must derive the same answer independently.

## Rationale

**Unit tests cannot validate LLM reasoning.** Mocking the LLM call and asserting on a fixed string tests nothing about whether the model produces correct output. The only meaningful test is one that calls the real model and checks the result.

**The air freight recommendation is uniquely suited to automated evaluation.** It has a single correct answer derivable from deterministic supply chain maths, making it binary-assertable in a way that most open-ended LLM outputs are not.

**Withholding `Air_Freight_Candidate` from the payload prevents shortcut reasoning.** If the field were included, the LLM could simply echo it rather than reason over revenue and risk data. Excluding it forces genuine inference.

**deepeval provides test infrastructure for LLM-specific assertions** (e.g., hallucination metrics, answer relevance) that can be added incrementally as the evaluation suite matures.

## Consequences

- `test_evals.py` requires a live LLM (local or production) and is therefore excluded from the standard CI test run. It is run separately as a quality gate before releases.
- The prompt must include the structured `**AIR FREIGHT SKU: <name>**` sentinel line, and any prompt changes that remove or reformat this line will break the eval test.
- A model change (e.g., upgrading from `claude-sonnet-4-6` to a future model) should trigger a re-run of the eval suite before deployment.
