from __future__ import annotations

import os

from llm_config import LLMConfig


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def load_llm_config_from_env() -> LLMConfig:
    _load_dotenv_if_available()
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("OPENAI_MODEL")

    missing = [
        key
        for key, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_BASE_URL", base_url),
            ("OPENAI_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    return LLMConfig(api_key=api_key or "", base_url=base_url or "", model=model or "")
