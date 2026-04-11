from __future__ import annotations

import argparse
from pathlib import Path

from bootstrap import load_llm_config_from_env
from config import GenerationConfig, build_generation_config
from generate import PromptInjectionViaHTMLTrap
from opentrap.traps.perception.prompt_injection_via_html.llm_html_generator import LLMHTMLGenerator


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Generate base and poisoned HTML for prompt_injection_via_html scenario",
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--content-type", required=True)
    parser.add_argument("--attack-intent", required=True)
    parser.add_argument("--location-temperature", type=float, default=0.0)
    parser.add_argument("--density-temperature", type=float, default=0.0)
    parser.add_argument("--diversity-temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--base-count", type=int, default=3)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def _build_config_from_args(args: argparse.Namespace) -> GenerationConfig:
    return build_generation_config(
        scenario=args.scenario,
        content_type=args.content_type,
        attack_intent=args.attack_intent,
        location_temperature=args.location_temperature,
        density_temperature=args.density_temperature,
        diversity_temperature=args.diversity_temperature,
        seed=args.seed,
        base_count=args.base_count,
        run_id=args.run_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _build_config_from_args(args)
    llm_cfg = load_llm_config_from_env()
    trap = PromptInjectionViaHTMLTrap(base_html_generator=LLMHTMLGenerator(llm_cfg))
    scenario_root = Path(__file__).resolve().parents[2]
    output_root = scenario_root / "run"
    run_dir = trap.run(config=config, output_base=output_root)
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
