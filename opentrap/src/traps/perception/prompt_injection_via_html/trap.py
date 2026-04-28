from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions import TrapActions
from config import GenerationConfig, build_generation_config
from evaluation import evaluate_prompt_injection_run
from generate import TrapDatasetGenerator
from llm_config import load_llm_config_from_env
from llm_html_generator import LLMHTMLGenerator

from opentrap.trap_contract import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec


@dataclass(frozen=True)
class TrapBindContext:
    data_dir: Path


@dataclass(frozen=True)
class TrapEvalContext:
    run_manifest_path: Path
    run_dir: Path
    report_path: Path
    trap_id: str
    status_emitter: object | None = None

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default_trap_id: str,
    ) -> TrapEvalContext:
        if isinstance(value, TrapEvalContext):
            return value
        if not isinstance(value, Mapping):
            raise RuntimeError("trap evaluation context must be a mapping")

        run_manifest_path = cls._read_path(value, "run_manifest_path")
        run_dir = cls._read_optional_path(value, "run_dir") or run_manifest_path.parent
        report_path = cls._read_optional_path(value, "report_path") or (run_dir / "report.json")
        trap_id_value = value.get("trap_id")
        trap_id = (
            trap_id_value
            if isinstance(trap_id_value, str) and trap_id_value
            else default_trap_id
        )
        status_emitter = value.get("status_emitter")

        return cls(
            run_manifest_path=run_manifest_path,
            run_dir=run_dir,
            report_path=report_path,
            trap_id=trap_id,
            status_emitter=status_emitter,
        )

    @staticmethod
    def _read_path(value: Mapping[str, Any], key: str) -> Path:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"trap evaluation context field '{key}' must be a non-empty string")
        return Path(raw)

    @staticmethod
    def _read_optional_path(value: Mapping[str, Any], key: str) -> Path | None:
        raw = value.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"trap evaluation context field '{key}' must be a non-empty string")
        return Path(raw)


@dataclass(frozen=True)
class TrapEvalResult:
    score: float
    details: Mapping[str, Any]


class Trap(
    TrapSpec[
        TrapBindContext,
        TrapActions,
        TrapEvalContext,
        TrapEvalResult,
    ]
):
    trap_id = ""
    fields = {
        "location_temperature": TrapFieldSpec(
            type="number",
            default=0.0,
            min=0.0,
            max=1.0,
            description="How often insertion location is randomized.",
        ),
        "density_temperature": TrapFieldSpec(
            type="number",
            default=0.0,
            min=0.0,
            max=1.0,
            description="How many injections are applied per poisoned variant.",
        ),
        "diversity_temperature": TrapFieldSpec(
            type="number",
            default=0.0,
            min=0.0,
            max=1.0,
            description="How many distinct attack types appear in a variant.",
        ),
        "base_count": TrapFieldSpec(
            type="integer",
            default=3,
            min=1,
            description="How many base documents to generate before poisoning.",
        ),
    }

    def __init__(self, dataset_generator: TrapDatasetGenerator | None = None) -> None:
        self._dataset_generator = dataset_generator

    def _resolve_dataset_generator(self) -> TrapDatasetGenerator:
        if self._dataset_generator is None:
            llm_cfg = load_llm_config_from_env()
            self._dataset_generator = TrapDatasetGenerator(
                base_html_generator=LLMHTMLGenerator(llm_cfg)
            )
        return self._dataset_generator

    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        dataset_generator = self._resolve_dataset_generator()
        generation_config = self._build_generation_config(
            shared_config=shared_config,
            trap_config=trap_config,
        )
        return dataset_generator.generate(
            config=generation_config,
            output_base=output_base,
        )

    def _build_generation_config(
        self,
        *,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
    ) -> GenerationConfig:
        return build_generation_config(
            scenario=shared_config.scenario,
            content_style=shared_config.content_style,
            trap_intent=shared_config.trap_intent,
            location_temperature=float(trap_config["location_temperature"]),
            density_temperature=float(trap_config["density_temperature"]),
            diversity_temperature=float(trap_config["diversity_temperature"]),
            seed=shared_config.seed,
            base_count=int(trap_config["base_count"]),
            run_id=None,
            samples=shared_config.samples,
        )

    def bind(self, context: TrapBindContext) -> TrapActions:
        return TrapActions(data_dir=context.data_dir)

    def build_cases(self, context: TrapCaseContext) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        for raw_line in context.metadata_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            if not isinstance(record, dict):
                continue
            file_id = record.get("file_id")
            filename = record.get("filename")
            if not isinstance(file_id, str) or not isinstance(filename, str):
                continue
            cases.append(
                {
                    "item_id": file_id,
                    "data_item": {
                        "id": file_id,
                        "path": str(context.data_dir / filename),
                    },
                    "metadata": record,
                }
            )
        return cases

    def evaluate(self, context: TrapEvalContext) -> TrapEvalResult:
        eval_context = TrapEvalContext.from_value(context, default_trap_id=self.trap_id)
        artifacts = evaluate_prompt_injection_run(
            run_manifest_path=eval_context.run_manifest_path,
            trap_id=eval_context.trap_id,
            status_emitter=eval_context.status_emitter,
        )

        success_rate = artifacts.summary.llm_judge_success_rate
        return TrapEvalResult(
            score=success_rate if success_rate is not None else 0.0,
            details={
                "status": "evaluated",
                "report_path": str(eval_context.report_path),
                "evaluation_jsonl_path": str(artifacts.evaluation_jsonl_path),
                "evaluation_csv_path": str(artifacts.evaluation_csv_path),
                "evaluation_summary_path": str(artifacts.evaluation_summary_path),
                "summary": artifacts.summary.to_dict(),
            },
        )
