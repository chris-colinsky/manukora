# ADR 0005: Tenacity for LLM Call Retry Logic

**Status:** Accepted  
**Date:** 2026-04-06

## Context

LLM API calls — both to Anthropic and to local inference endpoints — are subject to transient failures: rate limits, network timeouts, and momentary service unavailability. The S&OP briefing generation is a single blocking call in the request path, so a transient failure surfaces directly to the user as a 500 error.

We need a retry strategy for `_call_llm_with_retry()`.

## Decision

We use the `tenacity` library with exponential backoff:

```python
@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
```

We explicitly chose **not** to write a custom retry loop.

## Rationale

**Tenacity is purpose-built for this.** It handles jitter, backoff curves, attempt counting, and re-raise semantics correctly. A hand-rolled `for i in range(3)` loop routinely gets edge cases wrong (e.g., sleeping before the first retry, not re-raising the original exception).

**Three attempts with 2–30s backoff is appropriate for LLM APIs.** Anthropic rate limit errors typically resolve within seconds. More than 3 attempts would make the endpoint unacceptably slow for an interactive dashboard load.

**`reraise=True` preserves the original exception.** The FastAPI error handler receives the actual Anthropic or OpenAI exception, not a `RetryError` wrapper, which produces more useful error responses.

## Consequences

- Worst-case latency for a failed request is ~60 seconds (2s + 4s + 30s waits) before the exception propagates. This is acceptable given the 120s timeout on the Streamlit HTTP client.
- The retry decorator is applied at the transport level (`_call_llm_with_retry`), not at the `generate_briefing` level, so Langfuse tracing and structured logging execute only once regardless of retries.
