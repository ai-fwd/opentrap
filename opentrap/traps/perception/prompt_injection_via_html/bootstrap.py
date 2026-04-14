from __future__ import annotations

import os

from llm_config import LLMConfig

DEFAULT_OPENAI_URL = "https://api.openai.com"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _build_openai_base_url(openai_url: str) -> str:
    normalized_base = openai_url.strip().rstrip("/")
    if normalized_base.endswith("/v1/responses"):
        return normalized_base[: -len("/responses")]
    if normalized_base.endswith("/v1"):
        return normalized_base
    return f"{normalized_base}/v1"


def load_llm_config_from_env() -> LLMConfig:
    _load_dotenv_if_available()
    api_key = os.environ.get("OPENAI_API_KEY")
    openai_url = os.environ.get("OPENAI_URL") or DEFAULT_OPENAI_URL
    model = os.environ.get("OPENAI_MODEL")

    missing = [
        key
        for key, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    return LLMConfig(
        api_key=api_key or "",
        base_url=_build_openai_base_url(openai_url),
        model=model or "",
    )
