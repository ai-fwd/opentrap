"""Reusable OpenAI-compatible LLM judge support."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from opentrap.evaluation.scorers import normalize_metric_text


@dataclass(frozen=True)
class JudgeResult:
    """Structured LLM-as-judge output."""

    success: bool | None
    confidence: float | None
    reason: str | None
    model: str | None = None
    raw_response: str | None = None


def _default_openai_client_factory(llm_config: Any) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)


class LLMJudge:
    """Base OpenAI-compatible LLM judge with configurable rubric hooks."""

    def __init__(
        self,
        *,
        llm_config: Any | None = None,
        client_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._llm_config = llm_config
        self._client_factory = client_factory or _default_openai_client_factory
        self._client: Any = None

    def judge(
        self,
        *,
        trap_intent: str,
        baseline_output: str | None,
        observed_output: str | None,
        case_metadata: Mapping[str, Any],
        injection_type: str | None,
    ) -> JudgeResult:
        observed = normalize_metric_text(observed_output)
        if observed is None:
            return JudgeResult(
                success=None,
                confidence=None,
                reason="Judge skipped: observed output is missing or empty.",
                model=self._resolve_llm_config().model,
                raw_response=None,
            )
        intent = normalize_metric_text(trap_intent)
        if intent is None:
            return JudgeResult(
                success=None,
                confidence=None,
                reason="Judge skipped: trap intent is missing or empty.",
                model=self._resolve_llm_config().model,
                raw_response=None,
            )

        baseline = normalize_metric_text(baseline_output)
        messages = [
            {"role": "system", "content": self.system_rubric_prompt()},
            {
                "role": "user",
                "content": self.user_prompt(
                    trap_intent=intent,
                    baseline_output=baseline,
                    observed_output=observed,
                    case_metadata=case_metadata,
                    injection_type=injection_type,
                ),
            },
        ]

        raw_response: str | None = None
        try:
            raw_response = self._request_json_decision(messages)
            payload = self._parse_judge_json(raw_response)
            success, confidence, reason = self._normalize_judge_payload(payload)
            return JudgeResult(
                success=success,
                confidence=confidence,
                reason=reason,
                model=self._resolve_llm_config().model,
                raw_response=raw_response,
            )
        except Exception as exc:  # noqa: BLE001
            return JudgeResult(
                success=None,
                confidence=None,
                reason=f"Judge failed: {exc}",
                model=self._resolve_llm_config().model,
                raw_response=raw_response,
            )

    def system_rubric_prompt(self) -> str:
        raise NotImplementedError

    def user_prompt(
        self,
        *,
        trap_intent: str,
        baseline_output: str | None,
        observed_output: str,
        case_metadata: Mapping[str, Any],
        injection_type: str | None,
    ) -> str:
        raise NotImplementedError

    def required_response_keys(self) -> tuple[str, ...]:
        return ("success", "confidence", "reason")

    def _request_json_decision(self, messages: list[dict[str, str]]) -> str:
        client = self._load_client()
        model = self._resolve_llm_config().model

        strict_error: Exception | None = None
        try:
            strict_response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
            return self._extract_response_text(strict_response)
        except Exception as exc:  # noqa: BLE001
            strict_error = exc

        try:
            fallback_response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            return self._extract_response_text(fallback_response)
        except Exception as fallback_error:  # noqa: BLE001
            if strict_error is not None:
                raise RuntimeError(
                    "strict JSON call failed "
                    f"({strict_error}); fallback call failed ({fallback_error})"
                ) from fallback_error
            raise

    def _parse_judge_json(self, raw_text: str) -> dict[str, Any]:
        candidate = raw_text.strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as err:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("judge response was not valid JSON") from err
            payload = json.loads(candidate[start : end + 1])
        if not isinstance(payload, dict):
            raise RuntimeError("judge JSON response must be an object")
        return payload

    def _normalize_judge_payload(self, payload: Mapping[str, Any]) -> tuple[bool, float, str]:
        for key in self.required_response_keys():
            if key not in payload:
                raise RuntimeError(f"judge JSON response missing required key '{key}'")

        success_raw = payload.get("success")
        confidence_raw = payload.get("confidence")
        reason_raw = payload.get("reason")

        if not isinstance(success_raw, bool):
            raise RuntimeError("judge JSON field 'success' must be a boolean")
        if not isinstance(confidence_raw, int | float):
            raise RuntimeError("judge JSON field 'confidence' must be numeric")
        if not isinstance(reason_raw, str) or not reason_raw.strip():
            raise RuntimeError("judge JSON field 'reason' must be a non-empty string")

        confidence = float(confidence_raw)
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        return success_raw, confidence, reason_raw.strip()

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if isinstance(choices, Sequence) and choices:
            first_choice = choices[0]
            message = getattr(first_choice, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, Sequence) and not isinstance(content, str | bytes):
                text_fragments: list[str] = []
                for chunk in content:
                    if isinstance(chunk, str):
                        text_fragments.append(chunk)
                        continue
                    if isinstance(chunk, Mapping):
                        maybe_text = chunk.get("text")
                        if isinstance(maybe_text, str):
                            text_fragments.append(maybe_text)
                if text_fragments:
                    return "\n".join(text_fragments)
        raise RuntimeError("judge provider response did not contain text content")

    def _resolve_llm_config(self) -> Any:
        if self._llm_config is None:
            raise RuntimeError("LLM judge config was not provided")
        return self._llm_config

    def _load_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(self._resolve_llm_config())
        return self._client
