"""Prompt management: Langfuse-first loading with local Jinja2 fallback.

Prompts are stored as Jinja2 templates in the templates/ directory and
optionally published to Langfuse for versioning, A/B testing, and
observability.  At runtime the loader tries Langfuse first; if the
client is unavailable or the fetch fails, it falls back to the local
template file.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader

logger = structlog.get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

PROMPT_NAME_SYSTEM = "system_prompt"
PROMPT_NAME_USER = "user_prompt"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    keep_trailing_newline=False,
)


@dataclass
class PromptResult:
    """Container for a loaded prompt with optional Langfuse metadata."""

    text: str
    langfuse_prompt: Any | None = None
    config: dict[str, Any] = field(default_factory=dict)


def _get_prompt_label(prompt_name: str) -> str:
    """Read the deployment label for a prompt from prompt_labels.json.

    Args:
        prompt_name: The prompt identifier (e.g. 'system_prompt').

    Returns:
        The label string (e.g. 'production', 'staging').
    """
    labels_path = TEMPLATES_DIR / "prompt_labels.json"
    try:
        labels = json.loads(labels_path.read_text())
        return labels.get(prompt_name, labels.get("default", "production"))
    except Exception:
        return "production"


def _load_local_template(template_name: str, **variables: Any) -> str:
    """Render a local Jinja2 template with the given variables.

    Args:
        template_name: Filename of the template (e.g. 'system_prompt.j2').
        **variables: Template variables to substitute.

    Returns:
        The rendered template string.
    """
    template = _jinja_env.get_template(f"{template_name}.j2")
    return template.render(**variables)


def load_system_prompt(langfuse_client: Any | None = None) -> PromptResult:
    """Load the system prompt, trying Langfuse first with local fallback.

    Args:
        langfuse_client: An optional Langfuse client instance.

    Returns:
        A PromptResult with the compiled prompt text and metadata.
    """
    if langfuse_client is not None:
        label = _get_prompt_label(PROMPT_NAME_SYSTEM)
        try:
            prompt = langfuse_client.get_prompt(PROMPT_NAME_SYSTEM, label=label)
            compiled = prompt.compile()
            logger.info(
                "prompt_loaded_from_langfuse",
                prompt_name=PROMPT_NAME_SYSTEM,
                label=label,
                version=getattr(prompt, "version", "unknown"),
            )
            return PromptResult(
                text=compiled,
                langfuse_prompt=prompt,
                config=getattr(prompt, "config", {}),
            )
        except Exception as exc:
            logger.warning(
                "langfuse_prompt_fallback",
                error=str(exc),
                prompt_name=PROMPT_NAME_SYSTEM,
                label=label,
            )

    logger.debug("prompt_loaded_from_local", prompt_name=PROMPT_NAME_SYSTEM)
    return PromptResult(text=_load_local_template(PROMPT_NAME_SYSTEM))


def load_user_prompt(
    langfuse_client: Any | None = None,
    json_payload: str = "",
) -> PromptResult:
    """Load the user prompt, trying Langfuse first with local fallback.

    Args:
        langfuse_client: An optional Langfuse client instance.
        json_payload: The JSON-serialised S&OP data payload.

    Returns:
        A PromptResult with the compiled prompt text and metadata.
    """
    if langfuse_client is not None:
        label = _get_prompt_label(PROMPT_NAME_USER)
        try:
            prompt = langfuse_client.get_prompt(PROMPT_NAME_USER, label=label)
            compiled = prompt.compile(json_payload=json_payload)
            logger.info(
                "prompt_loaded_from_langfuse",
                prompt_name=PROMPT_NAME_USER,
                label=label,
                version=getattr(prompt, "version", "unknown"),
            )
            return PromptResult(
                text=compiled,
                langfuse_prompt=prompt,
                config=getattr(prompt, "config", {}),
            )
        except Exception as exc:
            logger.warning(
                "langfuse_prompt_fallback",
                error=str(exc),
                prompt_name=PROMPT_NAME_USER,
                label=label,
            )

    logger.debug("prompt_loaded_from_local", prompt_name=PROMPT_NAME_USER)
    return PromptResult(
        text=_load_local_template(PROMPT_NAME_USER, json_payload=json_payload)
    )
