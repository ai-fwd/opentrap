from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bootstrap import load_llm_config_from_env
from config import build_generation_config
from generate import PromptInjectionViaHTMLTrap
from llm_html_generator import LLMHTMLGenerator

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


def _run(shared_config: SharedConfig, trap_config: Mapping[str, Any], output_base: Path) -> Path:
    generation_config = build_generation_config(
        scenario=shared_config.scenario,
        content_style=shared_config.content_style,
        attack_intent=shared_config.attack_intent,
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


def get_trap_spec() -> TrapSpec:
    return TrapSpec(
        trap_id="perception/prompt_injection_via_html",
        fields={
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
        },
        run=_run,
    )
