from __future__ import annotations

import os
from pathlib import Path

from llm_config import LLMConfig

DEFAULT_OPENAI_URL = "https://api.openai.com"


def load_layered_env() -> None:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return

    project_root = Path(__file__).resolve().parents[4]

    shared = {
        k: v
        for k, v in dotenv_values(project_root / ".env.shared").items()
        if v is not None
    }
    local = {
        k: v
        for k, v in dotenv_values(project_root / ".env").items()
        if v is not None
    }

    merged = {
        **shared,
        **local,
        **os.environ,
    }

    os.environ.update(merged)


def _build_openai_base_url(openai_url: str) -> str:
    normalized_base = openai_url.strip().rstrip("/")
    if normalized_base.endswith("/v1/responses"):
        return normalized_base[: -len("/responses")]
    if normalized_base.endswith("/v1"):
        return normalized_base
    return f"{normalized_base}/v1"


def load_llm_config_from_env() -> LLMConfig:
    load_layered_env()
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
