# ADR 0001: Calculate First, Reason Second

**Status:** Accepted  
**Date:** 2026-04-06

## Context

We need to generate a weekly S&OP briefing that includes supply chain calculations (stock cover, reorder quantities, demand projections) and a natural-language narrative for non-technical executives.

The naive approach is to feed the raw CSV data directly to an LLM and ask it to both calculate the numbers and write the briefing.

## Decision

We adopt a strict two-stage pipeline:

1. **Calculate First (Python/Pandas):** All arithmetic — omnichannel totals, MoM growth rates, stock cover ratios, reorder quantities — is performed deterministically in `sop_engine.py` before the LLM is ever invoked.

2. **Reason Second (LLM):** The LLM receives a clean, pre-computed JSON payload. Its sole task is to translate verified numbers into executive-quality narrative, identify priorities, and make qualitative recommendations.

## Rationale

**LLMs hallucinate on arithmetic.** When asked to calculate a reorder quantity across 12 SKUs with lead-time adjustments, an LLM may produce plausible-sounding but numerically incorrect results. With purchasing decisions on the line, an arithmetic error is a business risk.

**Deterministic formulas are testable.** Pandas calculations can be unit-tested exhaustively with mock data. Every edge case (division by zero, new products, negative growth) can be covered and asserted. LLM outputs cannot be unit-tested this way.

**Separation enables independent evolution.** The calculation logic and the prompt engineering can be changed, tested, and deployed independently. A supply chain formula change does not require prompt re-engineering, and vice versa.

**The LLM is not replacing analysis — it's writing the memo.** The model's value is in synthesising pre-verified data into clear, prioritised narrative. This is what LLMs genuinely excel at.

## Consequences

- The FastAPI `sop_engine.py` module must be considered the single source of truth for all numbers.
- The JSON payload passed to the LLM must not include intermediate values that could confuse reasoning (e.g., `Air_Freight_Candidate` is withheld and used only as a deepeval ground-truth comparator).
- Any prompt change that references specific calculated fields must be validated against the Pydantic schema to ensure the field exists in the payload.
