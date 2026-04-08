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
from prompts import load_system_prompt, load_user_prompt

logger = structlog.get_logger(__name__)


def _call_anthropic(system_prompt: str, user_prompt: str) -> tuple[str, dict[str, Any]]:
    """Call the Anthropic API using the native SDK.

    Args:
        system_prompt: The system prompt string.
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    import anthropic  # noqa: PLC0415
    from anthropic.types import TextBlock  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    block = response.content[0]
    text = block.text if isinstance(block, TextBlock) else ""
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return text, usage


def _call_local(system_prompt: str, user_prompt: str) -> tuple[str, dict[str, Any]]:
    """Call a local OpenAI-compatible endpoint (LM Studio / vLLM).

    Args:
        system_prompt: The system prompt string.
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(base_url=config.LOCAL_LLM_BASE_URL, api_key="local")
    response = client.chat.completions.create(
        model=config.LOCAL_LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
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


def _log_retry(retry_state: Any) -> None:
    """Log each Tenacity retry attempt with context."""
    logger.warning(
        "llm_retry_attempt",
        attempt=retry_state.attempt_number,
        wait_seconds=round(retry_state.idle_for, 1) if retry_state.idle_for else 0,
        error=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
    before_sleep=_log_retry,
)
def _call_llm_with_retry(
    system_prompt: str, user_prompt: str
) -> tuple[str, dict[str, Any]]:
    """Invoke the appropriate LLM backend with exponential-backoff retry.

    Args:
        system_prompt: The system prompt string.
        user_prompt: The fully-rendered user prompt string.

    Returns:
        A tuple of (response_text, usage_dict).
    """
    if config.ENV == "production":
        return _call_anthropic(system_prompt, user_prompt)
    return _call_local(system_prompt, user_prompt)


def generate_briefing(payload: dict) -> str:
    """Generate the executive S&OP briefing from a pre-calculated JSON payload.

    Loads prompts from Langfuse (with local Jinja2 fallback), wraps the LLM
    call with Langfuse tracing and OpenTelemetry spans.

    Args:
        payload: The output of sop_engine.build_llm_payload().

    Returns:
        A Markdown string containing the full S&OP briefing.
    """
    tracer = telemetry.get_tracer()
    langfuse = telemetry.get_langfuse()

    # Load prompts (Langfuse-first, local fallback)
    system_result = load_system_prompt(langfuse_client=langfuse)
    json_payload_string = json.dumps(payload, indent=2, default=str)
    user_result = load_user_prompt(
        langfuse_client=langfuse, json_payload=json_payload_string
    )

    generation = None
    model_name = (
        "claude-sonnet-4-6" if config.ENV == "production" else config.LOCAL_LLM_MODEL
    )

    if langfuse:
        # Link system prompt as its own observation so Langfuse tracks
        # observation counts for both prompts independently.
        if system_result.langfuse_prompt is not None:
            sys_span = langfuse.start_observation(
                name="system-prompt",
                as_type="span",
                input=system_result.text,
                prompt=system_result.langfuse_prompt,
            )
            sys_span.end()

        gen_kwargs: dict[str, Any] = {
            "name": "sop-llm-call",
            "as_type": "generation",
            "model": model_name,
            "input": {"system": system_result.text, "user": user_result.text},
            "metadata": {
                "env": config.ENV,
                "system_prompt_source": (
                    "langfuse" if system_result.langfuse_prompt else "local"
                ),
                "user_prompt_source": (
                    "langfuse" if user_result.langfuse_prompt else "local"
                ),
            },
        }
        if user_result.langfuse_prompt is not None:
            gen_kwargs["prompt"] = user_result.langfuse_prompt
        generation = langfuse.start_observation(**gen_kwargs)

    logger.info(
        "llm_call_started",
        model=model_name,
        env=config.ENV,
        system_prompt_length=len(system_result.text),
        user_prompt_length=len(user_result.text),
    )

    start_time = time.monotonic()

    with tracer.start_as_current_span("llm.generate_briefing"):
        text, usage = _call_llm_with_retry(system_result.text, user_result.text)

    elapsed = time.monotonic() - start_time

    logger.info(
        "llm_call_complete",
        model=model_name,
        env=config.ENV,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        response_length=len(text),
        latency_seconds=round(elapsed, 2),
    )

    if generation and langfuse:
        generation.update(
            output=text,
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )
        generation.end()
        langfuse.flush()

    return text
