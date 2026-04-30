from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from opentrap.evaluation import (
    JudgeResult,
    LLMJudge,
    RougeLScoreScorer,
    emit_evaluation_phase,
    emit_evaluation_progress,
    write_evaluation_artifacts,
)
from opentrap.events import RunEvent


def _fake_chat_response(content: str) -> Any:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_evaluation_status_helpers_emit_phase_and_progress_events() -> None:
    events: list[RunEvent] = []
    emit_evaluation_phase(events.append, "started")
    emit_evaluation_progress(events.append, processed=1, total=3)
    emit_evaluation_progress(events.append, processed=2, total=3)
    emit_evaluation_progress(events.append, processed=3, total=3)

    assert events == [
        RunEvent(type="evaluate_phase", payload={"phase": "started", "detail": None}),
        RunEvent(type="evaluate_progress", payload={"processed": 1, "total": 3}),
        RunEvent(type="evaluate_progress", payload={"processed": 2, "total": 3}),
        RunEvent(type="evaluate_progress", payload={"processed": 3, "total": 3}),
    ]


def test_core_rouge_l_scorer_handles_identical_and_missing_text() -> None:
    scorer = RougeLScoreScorer()

    assert (
        scorer.score(
            baseline_output="The quick brown fox.",
            observed_output="The quick brown fox.",
        )
        == 1.0
    )
    assert scorer.score(baseline_output=None, observed_output="x") is None


def test_core_llm_judge_strict_json_fallback() -> None:
    class _FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("strict json mode unsupported")
            return _fake_chat_response(
                '{"success": true, "confidence": 0.9, "reason": "intent reflected"}'
            )

    class _Judge(LLMJudge):
        def system_rubric_prompt(self) -> str:
            return "Return JSON."

        def user_prompt(self, **kwargs: Any) -> str:
            return json.dumps(kwargs, default=str)

    completions = _FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    scorer = _Judge(
        llm_config=SimpleNamespace(
            api_key="test",
            base_url="https://example.test/v1",
            model="judge-model",
        ),
        client_factory=lambda _cfg: fake_client,
    )

    result = scorer.judge(
        trap_intent="flip sentiment",
        baseline_output="positive",
        observed_output="negative",
        case_metadata={},
        injection_type=None,
    )

    assert result == JudgeResult(
        success=True,
        confidence=0.9,
        reason="intent reflected",
        model="judge-model",
        raw_response='{"success": true, "confidence": 0.9, "reason": "intent reflected"}',
    )
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]


@dataclass(frozen=True)
class _Record:
    name: str
    metadata: dict[str, str]
    raw: str


def test_write_evaluation_artifacts_preserves_json_only_fields(tmp_path: Path) -> None:
    records = [_Record(name="case-1", metadata={"k": "v"}, raw="provider response")]
    summary = {"total_cases": 1}

    artifacts = write_evaluation_artifacts(
        run_dir=tmp_path,
        records=records,
        summary=summary,
        csv_fieldnames=["name", "metadata", "raw"],
        csv_exclude_fields={"raw"},
    )

    jsonl_payload = json.loads(artifacts.evaluation_jsonl_path.read_text(encoding="utf-8"))
    csv_header = artifacts.evaluation_csv_path.read_text(encoding="utf-8").splitlines()[0]
    summary_payload = json.loads(artifacts.evaluation_summary_path.read_text(encoding="utf-8"))

    assert jsonl_payload["raw"] == "provider response"
    assert csv_header == "name,metadata"
    assert summary_payload == summary
    assert artifacts.evaluation_report_html_path is None


def test_write_evaluation_artifacts_writes_optional_html_report(tmp_path: Path) -> None:
    artifacts = write_evaluation_artifacts(
        run_dir=tmp_path,
        records=[],
        summary={"total_cases": 0},
        csv_fieldnames=["name"],
        evaluation_report_html="<html><body>report</body></html>",
    )

    assert artifacts.evaluation_report_html_path == tmp_path / "evaluation_report.html"
    assert artifacts.evaluation_report_html_path.exists()
    assert (
        artifacts.evaluation_report_html_path.read_text(encoding="utf-8")
        == "<html><body>report</body></html>"
    )
