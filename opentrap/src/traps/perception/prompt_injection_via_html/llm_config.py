from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str


def _build_openai_base_url(openai_url: str) -> str:
    normalized_base = openai_url.strip().rstrip("/")
    if normalized_base.endswith("/v1/responses"):
        return normalized_base[: -len("/responses")]
    if normalized_base.endswith("/v1"):
        return normalized_base
    return f"{normalized_base}/v1"


def _find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("Could not locate repository root from llm_config.py path")


def _load_opentrap_env_file() -> None:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return

    env_path = _find_repo_root() / "opentrap" / ".env"
    if not env_path.exists():
        return

    loaded = dotenv_values(env_path)
    for key, value in loaded.items():
        if value is None:
            continue
        os.environ.setdefault(key, value)


def load_llm_config_from_env() -> LLMConfig:
    _load_opentrap_env_file()

    api_key = os.environ.get("OPENAI_API_KEY")
    openai_url = os.environ.get("OPENAI_URL")
    model = os.environ.get("OPENAI_MODEL")

    missing = [
        key
        for key, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_URL", openai_url),
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
