"""LLM factory: local OpenAI-compatible endpoint or production Anthropic Claude.

Uses the Factory pattern to switch between environments without heavyweight
abstraction layers (e.g. LiteLLM).  All LLM calls are wrapped with Tenacity
exponential backoff and Langfuse tracing.
"""

import json
import time
from typing import Any

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

import config
import telemetry

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "You are an expert Supply Chain & S&OP Director for a highly successful DTC honey brand. "
    "Your task is to review the weekly pre-calculated inventory data and write a concise, "
    "highly actionable S&OP briefing for the executive team. The briefing must take under "
    "5 minutes to read. Use a professional, data-driven, yet accessible tone. "
    "Use Markdown formatting for readability."
)

USER_PROMPT_TEMPLATE = """Below is the JSON payload containing the analyzed sales and inventory data for this week. It includes total sales, projected demand (accounting for MoM growth), stock risks, and mathematically calculated reorder quantities.

1. Write an Executive Summary of the past month's performance.
2. Highlight what sold well vs. what sold poorly. Specifically, call out the worst-performing SKU (dead stock) and reason about whether we should implement a discount or bundling strategy to free up working capital.
3. Create a 'Red Flags' section for SKUs falling below target cover. Note that projections are based on trailing 4-month momentum; explicitly remind the team to consider upcoming seasonality factors.
4. Make reorder recommendations for at least 3 SKUs. Use the 'Suggested_Reorder_Qty' provided, but write out the genuine business reasoning for *why* we are ordering that amount (e.g., referencing lead times, current pipeline, and target cover). If multiple items need reordering, prioritise them: reason about which one is the highest priority based on its revenue contribution (Revenue_M4) vs. its lead time, assuming a constrained cash-flow environment.
5. Acknowledge the 'Bioactive Blend' line as new Q1 2026 products. Explain to the team that to avoid over-ordering on an initial launch spike, we have conservatively modelled their future demand using their current M4 baseline rather than compounding their initial MoM growth.
6. **Strategic Priority (Air Freight):** Based on the data provided, identify the single most critical SKU that is currently at risk. Weigh its recent revenue contribution (Revenue_M4) against its stock risk. Make a recommendation on whether we should pay a premium to air-freight this specific item to protect top-line revenue and justify your choice logically.

   End this section with exactly this line (fill in the SKU name):
   **AIR FREIGHT SKU: <full SKU name here>**

DATA PAYLOAD:

{json_payload_string}"""


def _call_anthropic(user_prompt: str) -> tuple[str, dict[str, Any]]:
    """Call the Anthropic API using the native SDK.

    Args:
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return text, usage


def _call_local(user_prompt: str) -> tuple[str, dict[str, Any]]:
    """Call a local OpenAI-compatible endpoint (LM Studio / vLLM).

    Args:
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(base_url=config.LOCAL_LLM_BASE_URL, api_key="local")
    response = client.chat.completions.create(
        model=config.LOCAL_LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4096,
    )
    text = response.choices[0].message.content or ""
    usage_obj = response.usage
    usage = {
        "input_tokens": usage_obj.prompt_tokens if usage_obj else 0,
        "output_tokens": usage_obj.completion_tokens if usage_obj else 0,
    }
    return text, usage


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_llm_with_retry(user_prompt: str) -> tuple[str, dict[str, Any]]:
    """Invoke the appropriate LLM backend with exponential-backoff retry.

    Args:
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    if config.ENV == "production":
        return _call_anthropic(user_prompt)
    return _call_local(user_prompt)


def generate_briefing(payload: dict) -> str:
    """Generate the executive S&OP briefing from a pre-calculated JSON payload.

    Wraps the LLM call with Langfuse tracing (when configured) and
    OpenTelemetry spans.

    Args:
        payload: The output of sop_engine.build_llm_payload().

    Returns:
        A Markdown string containing the full S&OP briefing.
    """
    tracer = telemetry.get_tracer()
    langfuse = telemetry.get_langfuse()

    json_payload_string = json.dumps(payload, indent=2, default=str)
    user_prompt = USER_PROMPT_TEMPLATE.format(json_payload_string=json_payload_string)

    generation = None
    if langfuse:
        trace_obj = langfuse.trace(name="generate-sop-briefing")
        generation = trace_obj.generation(
            name="sop-llm-call",
            model=(
                "claude-sonnet-4-6"
                if config.ENV == "production"
                else config.LOCAL_LLM_MODEL
            ),
            input={"system": SYSTEM_PROMPT, "user": user_prompt},
        )

    start_time = time.monotonic()

    with tracer.start_as_current_span("llm.generate_briefing"):
        text, usage = _call_llm_with_retry(user_prompt)

    elapsed = time.monotonic() - start_time

    logger.info(
        "llm_call_complete",
        env=config.ENV,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        latency_seconds=round(elapsed, 2),
    )

    if generation and langfuse:
        generation.end(
            output=text,
            usage={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )
        langfuse.flush()

    return text
