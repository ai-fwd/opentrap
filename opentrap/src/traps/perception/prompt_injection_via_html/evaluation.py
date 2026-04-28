"""Trap-local evaluation pipeline for HTML prompt injection.

Methodology alignment with the paper:
- ROUGE-L is used as a lexical overlap signal between clean and injected outputs.
  It captures surface-form drift: word choice and phrasing changes that appear
  when prompt injection alters what the model says.
- SBERT cosine similarity is used as a semantic drift signal. It captures deeper
  meaning changes even when wording overlap is high.
- The paper used manual annotations to mark whether injected intent succeeded.
  OpenTrap replaces this manual step with an LLM-as-judge interface keyed by
  `trap_intent` from trap metadata.

Implementation note:
- Actual ROUGE-L / SBERT / LLM-judge logic is deliberately stubbed in this change.
  The interfaces are stable, and future work can plug real implementations in
  without changing artifact formats.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PromptInjectionEvaluationInputRecord:
    """Normalized per-case inputs before scoring."""

    run_id: str
    trap_id: str
    case_index: int
    item_id: str
    clean_output: str | None
    injected_output: str | None
    trap_intent: str
    injection_type: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PromptInjectionJudgeResult:
    """Structured LLM-as-judge output."""

    success: bool | None
    confidence: float | None
    reason: str | None


@dataclass(frozen=True)
class PromptInjectionEvaluationOutputRecord:
    """Scored per-case evaluation record persisted to JSONL/CSV."""

    run_id: str
    trap_id: str
    case_index: int
    item_id: str
    trap_intent: str
    injection_type: str | None
    rouge_l_f1: float | None
    sbert_cosine_similarity: float | None
    llm_judge_success: bool | None
    llm_judge_confidence: float | None
    llm_judge_reason: str | None
    clean_output: str | None
    injected_output: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PromptInjectionEvaluationSummary:
    """Aggregate metrics used for quick run-level analysis."""

    total_cases: int
    judged_cases: int
    llm_judge_success_count: int
    llm_judge_success_rate: float | None
    average_rouge_l_f1: float | None
    average_sbert_cosine_similarity: float | None
    grouped_averages_by_injection_type: dict[str, dict[str, float | None]]
    grouped_success_rate_by_injection_type: dict[str, dict[str, float | int | None]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptInjectionEvaluationArtifacts:
    """Paths and summary returned by trap evaluation."""

    evaluation_jsonl_path: Path
    evaluation_csv_path: Path
    evaluation_summary_path: Path
    summary: PromptInjectionEvaluationSummary


class RougeLScorer(Protocol):
    """Lexical similarity scorer interface for clean vs injected outputs."""

    def score(self, *, clean_output: str, injected_output: str) -> float | None: ...


class SbertSimilarityScorer(Protocol):
    """Semantic similarity scorer interface for clean vs injected outputs."""

    def score(self, *, clean_output: str, injected_output: str) -> float | None: ...


class LlmJudgeScorer(Protocol):
    """LLM-as-judge interface for trap-intent success."""

    def judge(
        self,
        *,
        trap_intent: str,
        clean_output: str | None,
        injected_output: str | None,
    ) -> PromptInjectionJudgeResult: ...


class StubRougeLScorer:
    """Placeholder implementation that defers ROUGE-L work to follow-up changes."""

    def score(self, *, clean_output: str, injected_output: str) -> float | None:
        del clean_output, injected_output
        return None


class StubSbertSimilarityScorer:
    """Placeholder implementation that defers SBERT work to follow-up changes."""

    def score(self, *, clean_output: str, injected_output: str) -> float | None:
        del clean_output, injected_output
        return None


class StubLlmJudgeScorer:
    """Placeholder LLM-as-judge implementation.

    The interface already carries the required context (`trap_intent`, clean output,
    injected output). Later work can call an LLM and return a structured judgment.
    """

    def judge(
        self,
        *,
        trap_intent: str,
        clean_output: str | None,
        injected_output: str | None,
    ) -> PromptInjectionJudgeResult:
        del trap_intent, clean_output, injected_output
        return PromptInjectionJudgeResult(success=None, confidence=None, reason=None)


def evaluate_prompt_injection_run(
    *,
    run_manifest_path: Path,
    trap_id: str,
    rouge_scorer: RougeLScorer | None = None,
    sbert_scorer: SbertSimilarityScorer | None = None,
    llm_judge_scorer: LlmJudgeScorer | None = None,
) -> PromptInjectionEvaluationArtifacts:
    """Evaluate one finalized run for this trap and persist JSONL/CSV/summary artifacts."""
    manifest_payload = _load_json_object(run_manifest_path)
    run_dir = run_manifest_path.parent
    run_id = _require_string(manifest_payload, "run_id")
    trap_entry = _find_trap_entry(manifest_payload, trap_id=trap_id)

    observed_outputs = _load_observed_outputs(run_dir / "observations.jsonl")
    input_records = _build_input_records(
        run_id=run_id,
        trap_id=trap_id,
        trap_entry=trap_entry,
        observed_outputs=observed_outputs,
    )

    rouge_impl = rouge_scorer or StubRougeLScorer()
    sbert_impl = sbert_scorer or StubSbertSimilarityScorer()
    judge_impl = llm_judge_scorer or StubLlmJudgeScorer()

    output_records = _score_input_records(
        input_records=input_records,
        rouge_scorer=rouge_impl,
        sbert_scorer=sbert_impl,
        llm_judge_scorer=judge_impl,
    )
    summary = _build_summary(output_records)

    evaluation_jsonl_path = run_dir / "evaluation.jsonl"
    evaluation_csv_path = run_dir / "evaluation.csv"
    evaluation_summary_path = run_dir / "evaluation_summary.json"

    _write_evaluation_jsonl(evaluation_jsonl_path, output_records)
    _write_evaluation_csv(evaluation_csv_path, output_records)
    evaluation_summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )

    return PromptInjectionEvaluationArtifacts(
        evaluation_jsonl_path=evaluation_jsonl_path,
        evaluation_csv_path=evaluation_csv_path,
        evaluation_summary_path=evaluation_summary_path,
        summary=summary,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return payload


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"run manifest field '{key}' must be a non-empty string")
    return value


def _find_trap_entry(payload: Mapping[str, Any], *, trap_id: str) -> dict[str, Any]:
    traps_raw = payload.get("traps")
    if not isinstance(traps_raw, list):
        raise RuntimeError("run manifest field 'traps' must be a list")
    for entry in traps_raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("trap_id") == trap_id:
            return entry
    raise RuntimeError(f"trap '{trap_id}' was not found in run manifest")


def _load_observed_outputs(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}

    observed_by_case_index: dict[int, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            continue
        case_index = value.get("case_index")
        content = value.get("content")
        if not isinstance(case_index, int) or case_index < 0:
            continue
        if not isinstance(content, str):
            continue
        # Keep the latest observed output for each case index.
        observed_by_case_index[case_index] = content
    return observed_by_case_index


def _as_case_list(trap_entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_cases = trap_entry.get("cases")
    if not isinstance(raw_cases, list):
        raise RuntimeError("trap entry must include a 'cases' list")
    normalized: list[dict[str, Any]] = []
    for case in raw_cases:
        if isinstance(case, dict):
            normalized.append(case)
    return normalized


def _build_input_records(
    *,
    run_id: str,
    trap_id: str,
    trap_entry: Mapping[str, Any],
    observed_outputs: Mapping[int, str],
) -> list[PromptInjectionEvaluationInputRecord]:
    cases = _as_case_list(trap_entry)

    clean_case_by_file_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        metadata = case.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("is_poisoned") is not False:
            continue
        file_id = metadata.get("file_id")
        if isinstance(file_id, str) and file_id:
            clean_case_by_file_id[file_id] = case

    input_records: list[PromptInjectionEvaluationInputRecord] = []
    for case in cases:
        metadata_raw = case.get("metadata")
        if not isinstance(metadata_raw, Mapping):
            continue
        if metadata_raw.get("is_poisoned") is not True:
            continue

        case_index = case.get("case_index")
        if not isinstance(case_index, int) or case_index < 0:
            continue

        item_id = case.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            item_id = str(metadata_raw.get("file_id") or f"case-{case_index}")

        base_file_id = metadata_raw.get("base_file_id")
        clean_case = (
            clean_case_by_file_id.get(base_file_id)
            if isinstance(base_file_id, str)
            else None
        )
        clean_case_index = clean_case.get("case_index") if isinstance(clean_case, dict) else None
        clean_output = (
            observed_outputs.get(clean_case_index)
            if isinstance(clean_case_index, int) and clean_case_index >= 0
            else None
        )
        injected_output = observed_outputs.get(case_index)

        trap_intent = metadata_raw.get("trap_intent")
        if not isinstance(trap_intent, str):
            trap_intent = ""

        attack_types = metadata_raw.get("attack_types")
        injection_type = _normalize_injection_type(attack_types)

        input_records.append(
            PromptInjectionEvaluationInputRecord(
                run_id=run_id,
                trap_id=trap_id,
                case_index=case_index,
                item_id=item_id,
                clean_output=clean_output,
                injected_output=injected_output,
                trap_intent=trap_intent,
                injection_type=injection_type,
                metadata=dict(metadata_raw),
            )
        )
    return input_records


def _normalize_injection_type(raw_attack_types: object) -> str | None:
    if not isinstance(raw_attack_types, Sequence) or isinstance(raw_attack_types, str):
        return None
    attack_types: list[str] = []
    for value in raw_attack_types:
        if isinstance(value, str) and value:
            attack_types.append(value)
    if not attack_types:
        return None
    return "+".join(attack_types)


def _score_input_records(
    *,
    input_records: Sequence[PromptInjectionEvaluationInputRecord],
    rouge_scorer: RougeLScorer,
    sbert_scorer: SbertSimilarityScorer,
    llm_judge_scorer: LlmJudgeScorer,
) -> list[PromptInjectionEvaluationOutputRecord]:
    outputs: list[PromptInjectionEvaluationOutputRecord] = []
    for record in input_records:
        rouge_l_f1 = None
        sbert_cosine_similarity = None
        if isinstance(record.clean_output, str) and isinstance(record.injected_output, str):
            rouge_l_f1 = rouge_scorer.score(
                clean_output=record.clean_output,
                injected_output=record.injected_output,
            )
            sbert_cosine_similarity = sbert_scorer.score(
                clean_output=record.clean_output,
                injected_output=record.injected_output,
            )

        judge_result = llm_judge_scorer.judge(
            trap_intent=record.trap_intent,
            clean_output=record.clean_output,
            injected_output=record.injected_output,
        )

        outputs.append(
            PromptInjectionEvaluationOutputRecord(
                run_id=record.run_id,
                trap_id=record.trap_id,
                case_index=record.case_index,
                item_id=record.item_id,
                trap_intent=record.trap_intent,
                injection_type=record.injection_type,
                rouge_l_f1=rouge_l_f1,
                sbert_cosine_similarity=sbert_cosine_similarity,
                llm_judge_success=judge_result.success,
                llm_judge_confidence=judge_result.confidence,
                llm_judge_reason=judge_result.reason,
                clean_output=record.clean_output,
                injected_output=record.injected_output,
                metadata=record.metadata,
            )
        )
    return outputs


def _average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _build_summary(
    records: Sequence[PromptInjectionEvaluationOutputRecord],
) -> PromptInjectionEvaluationSummary:
    total_cases = len(records)
    judged_cases = len([record for record in records if record.llm_judge_success is not None])
    success_count = len([record for record in records if record.llm_judge_success is True])
    success_rate = (success_count / judged_cases) if judged_cases > 0 else None

    rouge_values = [record.rouge_l_f1 for record in records if record.rouge_l_f1 is not None]
    sbert_values = [
        record.sbert_cosine_similarity
        for record in records
        if record.sbert_cosine_similarity is not None
    ]

    grouped_records: dict[str, list[PromptInjectionEvaluationOutputRecord]] = defaultdict(list)
    for record in records:
        if isinstance(record.injection_type, str) and record.injection_type:
            grouped_records[record.injection_type].append(record)

    grouped_averages: dict[str, dict[str, float | None]] = {}
    grouped_success_rates: dict[str, dict[str, float | int | None]] = {}
    for injection_type, grouped in sorted(grouped_records.items()):
        grouped_rouge = [record.rouge_l_f1 for record in grouped if record.rouge_l_f1 is not None]
        grouped_sbert = [
            record.sbert_cosine_similarity
            for record in grouped
            if record.sbert_cosine_similarity is not None
        ]
        grouped_judged = [record for record in grouped if record.llm_judge_success is not None]
        grouped_success = [record for record in grouped if record.llm_judge_success is True]

        grouped_averages[injection_type] = {
            "average_rouge_l_f1": _average(grouped_rouge),
            "average_sbert_cosine_similarity": _average(grouped_sbert),
        }
        grouped_success_rates[injection_type] = {
            "judged_cases": len(grouped_judged),
            "llm_judge_success_count": len(grouped_success),
            "llm_judge_success_rate": (
                len(grouped_success) / len(grouped_judged) if grouped_judged else None
            ),
        }

    return PromptInjectionEvaluationSummary(
        total_cases=total_cases,
        judged_cases=judged_cases,
        llm_judge_success_count=success_count,
        llm_judge_success_rate=success_rate,
        average_rouge_l_f1=_average([value for value in rouge_values if value is not None]),
        average_sbert_cosine_similarity=_average(
            [value for value in sbert_values if value is not None]
        ),
        grouped_averages_by_injection_type=grouped_averages,
        grouped_success_rate_by_injection_type=grouped_success_rates,
    )


def _write_evaluation_jsonl(
    path: Path,
    records: Sequence[PromptInjectionEvaluationOutputRecord],
) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_record_to_json_payload(record), ensure_ascii=False) + "\n")


def _write_evaluation_csv(
    path: Path,
    records: Sequence[PromptInjectionEvaluationOutputRecord],
) -> None:
    fieldnames = [
        "run_id",
        "trap_id",
        "case_index",
        "item_id",
        "trap_intent",
        "injection_type",
        "rouge_l_f1",
        "sbert_cosine_similarity",
        "llm_judge_success",
        "llm_judge_confidence",
        "llm_judge_reason",
        "clean_output",
        "injected_output",
        "metadata",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            payload = _record_to_json_payload(record)
            payload["metadata"] = json.dumps(record.metadata, ensure_ascii=False, sort_keys=True)
            writer.writerow(payload)


def _record_to_json_payload(record: PromptInjectionEvaluationOutputRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "trap_id": record.trap_id,
        "case_index": record.case_index,
        "item_id": record.item_id,
        "trap_intent": record.trap_intent,
        "injection_type": record.injection_type,
        "rouge_l_f1": record.rouge_l_f1,
        "sbert_cosine_similarity": record.sbert_cosine_similarity,
        "llm_judge_success": record.llm_judge_success,
        "llm_judge_confidence": record.llm_judge_confidence,
        "llm_judge_reason": record.llm_judge_reason,
        "clean_output": record.clean_output,
        "injected_output": record.injected_output,
        "metadata": record.metadata,
    }
