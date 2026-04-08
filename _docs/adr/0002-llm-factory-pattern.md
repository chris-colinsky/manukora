# ADR 0002: LLM Factory Pattern for Local/Production Switching

**Status:** Accepted  
**Date:** 2026-04-06

## Context

The system needs to run in two distinct environments:

- **Local development:** Engineers run inference locally via LM Studio or vLLM, using an OpenAI-compatible API at `http://localhost:1234/v1`. No Anthropic API key is needed and latency is acceptable for development iteration.
- **Production:** The system calls Anthropic's Claude API (`claude-sonnet-4-6`) with real credentials.

We need a clean way to switch between these without duplicating prompt logic or introducing heavyweight abstractions.

## Decision

We implement a lightweight factory in `llm_service.py`. A single `ENV` environment variable (`local` or `production`) determines which backend is invoked. The factory exposes one public function — `generate_briefing()` — and hides all backend-specific SDK calls behind internal `_call_anthropic()` and `_call_local()` functions.

We explicitly chose **not** to use LiteLLM, LangChain, or similar unified LLM frameworks.

## Rationale

**LiteLLM and LangChain add significant dependency weight and abstraction overhead** for a use case that requires only one model in production and one in development. The factory pattern achieves the same routing in ~30 lines of code with no additional dependencies.

**The OpenAI-compatible endpoint means local inference requires only the `openai` SDK**, which is already a common dependency. No separate local SDK is needed.

**Prompt logic is environment-agnostic.** `SYSTEM_PROMPT` and `USER_PROMPT_TEMPLATE` are defined once and passed identically to both backends. Switching environments cannot accidentally change what the model is asked to do.

## Consequences

- Adding a third LLM backend (e.g., Google Gemini) requires adding a new `_call_*` function and extending the `ENV` branch — a small, localised change.
- The `LOCAL_LLM_MODEL` env var must be set to match whatever model is loaded in LM Studio; there is no validation that the local model is capable of producing the expected output format.
- Integration tests that call `generate_briefing()` must either mock the factory or have a local LLM running.
