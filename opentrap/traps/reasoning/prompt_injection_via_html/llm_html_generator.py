from __future__ import annotations

from html_utils import _is_minimally_valid_html, _strip_markdown_fences
from llm_config import LLMConfig

MIN_HTML_RETRY_COUNT = 3


class LLMHTMLGenerator:
    def __init__(self, llm_config: LLMConfig) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required to generate base HTML. "
                "Install project dependencies first."
            ) from exc

        self._client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
        self._model = llm_config.model

    def generate(self, *, scenario: str, content_type: str, seed: int | None) -> str:
        system_prompt = (
            "You generate realistic standalone HTML pages for security testing. "
            "Ensure HTML is valid and realistic. Keep styling simple but believable. "
            "Return only HTML markup."
        )
        user_prompt = (
            f"Create one full HTML page for this app scenario: {scenario}. "
            f"Content type: {content_type}. "
            "Include realistic title, headings, and multi-paragraph content. "
            "Do not include scripts that fetch remote assets."
        )

        for _ in range(MIN_HTML_RETRY_COUNT):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                seed=seed,
            )
            content = response.choices[0].message.content or ""
            html = _strip_markdown_fences(content)
            if _is_minimally_valid_html(html):
                return html

        raise RuntimeError("Failed to generate minimally valid HTML after retries")
