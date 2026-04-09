#!/usr/bin/env python3
"""Push local prompt templates to Langfuse as new versions.

Usage:
    # Push all prompts with 'production' label
    make push-prompt

    # Push a single prompt
    make push-prompt ARGS="--prompt system_prompt"

    # Push with a specific label
    make push-prompt ARGS="--label staging"

    # Push with a commit message
    make push-prompt ARGS="--prompt user_prompt -m 'Updated air freight instructions'"

Requires LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and LANGFUSE_HOST
environment variables (reads from backend/.env automatically).
"""

import argparse
import json
import os
from pathlib import Path

from langfuse import Langfuse

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
TEMPLATES_DIR = BACKEND_DIR / "templates"

PROMPTS = {
    "system_prompt": {
        "name": "system_prompt",
        "template": TEMPLATES_DIR / "system_prompt.j2",
    },
    "user_prompt": {
        "name": "user_prompt",
        "template": TEMPLATES_DIR / "user_prompt.j2",
    },
}

PROMPT_CONFIGS_PATH = TEMPLATES_DIR / "prompt_configs.json"


def load_prompt_configs() -> dict:
    """Load prompt configuration metadata from prompt_configs.json.

    Returns:
        A dictionary mapping prompt names to their config.
    """
    try:
        return json.loads(PROMPT_CONFIGS_PATH.read_text())
    except Exception as exc:
        print(f"Warning: Could not load prompt_configs.json: {exc}")
        return {}


def push_prompt(
    client: Langfuse,
    key: str,
    labels: list[str],
    message: str | None,
    configs: dict,
) -> None:
    """Push a single prompt template to Langfuse.

    Args:
        client: An authenticated Langfuse client.
        key: The prompt key (e.g. 'system_prompt').
        labels: List of labels to apply (e.g. ['production']).
        message: Optional commit message.
        configs: The full prompt_configs dict.
    """
    spec = PROMPTS[key]
    template_path = Path(str(spec["template"]))
    prompt_name = str(spec["name"])

    template_content = template_path.read_text()
    prompt_config = configs.get(key)

    create_kwargs: dict = {
        "name": prompt_name,
        "prompt": template_content,
        "type": "text",
        "labels": labels,
    }
    if message:
        create_kwargs["commit_message"] = message
    if prompt_config is not None:
        create_kwargs["config"] = prompt_config

    prompt = client.create_prompt(**create_kwargs)
    version = getattr(prompt, "version", "?")
    print(f"  Pushed '{prompt_name}' v{version} with labels {labels}")


def _load_dotenv(env_file: str = ".env") -> None:
    """Load an env file into os.environ so Langfuse SDK can read credentials.

    Args:
        env_file: Filename relative to the backend directory (e.g. '.env', '..env.production').
    """
    env_path = BACKEND_DIR / env_file
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    else:
        print(f"Warning: {env_path} not found")


def main() -> None:
    """Parse arguments and push prompts to Langfuse."""
    parser = argparse.ArgumentParser(description="Push prompts to Langfuse")
    parser.add_argument(
        "--prompt",
        choices=list(PROMPTS.keys()),
        help="Push a specific prompt (default: all)",
    )
    parser.add_argument(
        "--label",
        default="production",
        help="Label to apply (default: production)",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Commit message for the prompt version",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file to load from backend/ (default: .env, use ..env.production for cloud)",
    )
    args = parser.parse_args()

    _load_dotenv(args.env_file)

    configs = load_prompt_configs()
    client = Langfuse()
    labels = [args.label]

    keys = [args.prompt] if args.prompt else list(PROMPTS.keys())

    print(f"Pushing {len(keys)} prompt(s) to Langfuse...")
    for key in keys:
        push_prompt(client, key, labels, args.message, configs)

    client.flush()
    print("Done.")


if __name__ == "__main__":
    main()
