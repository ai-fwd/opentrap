from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bootstrap import load_llm_config_from_env
from config import build_generation_config
from generate import PromptInjectionViaHTMLTrap
from llm_html_generator import LLMHTMLGenerator

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


@dataclass(frozen=True)
class TrapRunContext:
    data_dir: Path


class TrapActions:
    def __init__(self, *, data_dir: Path) -> None:
        self._data_dir = data_dir

    def get_data_for_selector(self, selector: Any) -> str:
        items = self._sorted_items()
        if not items:
            raise RuntimeError("no trap data items are available")
        digest = hashlib.sha256(str(selector).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % len(items)
        return items[index].read_text(encoding="utf-8")

    def _sorted_items(self) -> tuple[Path, ...]:
        return tuple(
            sorted(
                (
                    path
                    for path in self._data_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in {".htm", ".html"}
                ),
                key=lambda path: path.name,
            )
        )


@dataclass(frozen=True)
class TrapEvalContext:
    report_path: Path


@dataclass(frozen=True)
class TrapEvalResult:
    score: float
    details: Mapping[str, Any]


class Trap(
    TrapSpec[
        TrapRunContext,
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

    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        generation_config = build_generation_config(
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

        llm_cfg = load_llm_config_from_env()
        trap = PromptInjectionViaHTMLTrap(base_html_generator=LLMHTMLGenerator(llm_cfg))
        return trap.run(config=generation_config, output_base=output_base)

    def run(self, context: TrapRunContext) -> TrapActions:
        return TrapActions(data_dir=context.data_dir)

    def evaluate(self, context: TrapEvalContext) -> TrapEvalResult:
        return TrapEvalResult(
            score=0.0,
            details={
                "status": "not_evaluated",
                "report_path": str(context.report_path),
            },
        )
