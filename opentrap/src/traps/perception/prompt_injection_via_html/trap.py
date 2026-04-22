from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions import TrapActions
from config import GenerationConfig, build_generation_config
from generate import TrapDatasetGenerator
from llm_config import load_llm_config_from_env
from llm_html_generator import LLMHTMLGenerator

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


@dataclass(frozen=True)
class TrapBindContext:
    data_dir: Path


@dataclass(frozen=True)
class TrapEvalContext:
    report_path: Path


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
        if dataset_generator is None:
            llm_cfg = load_llm_config_from_env()
            dataset_generator = TrapDatasetGenerator(
                base_html_generator=LLMHTMLGenerator(llm_cfg)
            )
        self._dataset_generator = dataset_generator

    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        generation_config = self._build_generation_config(
            shared_config=shared_config,
            trap_config=trap_config,
        )
        return self._dataset_generator.generate(
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

    def evaluate(self, context: TrapEvalContext) -> TrapEvalResult:
        return TrapEvalResult(
            score=0.0,
            details={
                "status": "not_evaluated",
                "report_path": str(context.report_path),
            },
        )
