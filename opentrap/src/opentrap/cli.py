from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentrap.config_loader import (
    AttackConfigError,
    build_initial_config,
    load_attack_config,
    write_attack_config,
)
from opentrap.trap_contract import SharedConfig, TrapSpec
from opentrap.trap_registry import TrapRegistryError, build_trap_registry

DEFAULT_TRAPS_DIR = Path(__file__).resolve().parents[2] / "traps"
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_STATE_DIR = Path(".opentrap")
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "opentrap.yaml"
DEFAULT_SAMPLES_DIR = DEFAULT_STATE_DIR / "samples"


def _resolve_trap_ref(trap_ref: str) -> str:
    raw = trap_ref.strip()
    if "/" not in raw:
        raise ValueError("trap must be in 'target/name' format")
    target, trap_name = raw.split("/", 1)
    target = target.strip()
    trap_name = trap_name.strip()
    if not target:
        raise ValueError("trap target cannot be empty")
    if not trap_name:
        raise ValueError("trap name cannot be empty")
    return f"{target}/{trap_name}"


def _load_registry() -> dict[str, TrapSpec] | None:
    try:
        return build_trap_registry(DEFAULT_TRAPS_DIR)
    except TrapRegistryError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty.", file=sys.stderr)


def _prompt_seed(prompt: str) -> int | None:
    while True:
        value = input(prompt).strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            print("Seed must be an integer or blank.", file=sys.stderr)


def build_attack_report(
    trap_ids: list[str],
    requested: str | None,
    results: list[dict[str, Any]],
) -> dict[str, object]:
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "requested": requested,
        "trap_count": len(trap_ids),
        "trap_ids": trap_ids,
        "outcome": "success",
        "results": results,
    }


def cmd_list(args: argparse.Namespace) -> int:
    registry = _load_registry()
    if registry is None:
        return 1

    traps = sorted(registry)
    if args.target:
        prefix = f"{args.target.strip().lower()}/"
        traps = [trap_id for trap_id in traps if trap_id.lower().startswith(prefix)]

    for trap_id in traps:
        print(trap_id)
    return 0


def cmd_init(_: argparse.Namespace) -> int:
    registry = _load_registry()
    if registry is None:
        return 1

    shared = SharedConfig(
        scenario=_prompt_non_empty("Scenario: "),
        content_style=_prompt_non_empty("Content style: "),
        attack_intent=_prompt_non_empty("Attack intent: "),
        seed=_prompt_seed("Seed (optional integer): "),
    )

    try:
        payload = build_initial_config(shared, registry)
    except AttackConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_attack_config(DEFAULT_CONFIG_PATH, payload)
    DEFAULT_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Created config file: {DEFAULT_CONFIG_PATH}")
    print(f"Created samples directory: {DEFAULT_SAMPLES_DIR}")
    print(
        "Add one or more representative source examples to the samples directory. "
        "Examples are optional; if present, they guide generation style and structure."
    )
    return 0


def cmd_attack(args: argparse.Namespace) -> int:
    registry = _load_registry()
    if registry is None:
        return 1

    available = sorted(registry)
    trap_ref = args.trap

    if trap_ref is None:
        selected = available
        default_output = DEFAULT_RUNS_DIR / "all.json"
    else:
        try:
            resolved = _resolve_trap_ref(trap_ref)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if resolved not in registry:
            print(f"trap '{resolved}' was not found", file=sys.stderr)
            return 1
        selected = [resolved]
        default_output = DEFAULT_RUNS_DIR / f"{resolved.replace('/', '__')}.json"

    try:
        loaded = load_attack_config(
            DEFAULT_CONFIG_PATH,
            registry,
            samples_dir=DEFAULT_SAMPLES_DIR,
        )
    except AttackConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    for trap_id in selected:
        try:
            output_base = DEFAULT_RUNS_DIR / trap_id.replace("/", "__")
            artifact_path = registry[trap_id].run(
                loaded.shared,
                loaded.trap_configs[trap_id],
                output_base,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"trap '{trap_id}' failed: {exc}", file=sys.stderr)
            return 1
        results.append(
            {
                "trap_id": trap_id,
                "artifact_path": str(artifact_path),
            }
        )

    report = build_attack_report(trap_ids=selected, requested=trap_ref, results=results)
    output_path = Path(args.output) if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opentrap", description="OpenTrap red-team CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List available traps")
    list_parser.add_argument("--target", default=None)
    list_parser.set_defaults(handler=cmd_list)

    init_parser = subparsers.add_parser("init", help="Create .opentrap/opentrap.yaml")
    init_parser.set_defaults(handler=cmd_init)

    attack_parser = subparsers.add_parser(
        "attack",
        help="Run all traps or one specific target/name trap",
    )
    attack_parser.add_argument("trap", nargs="?", default=None)
    attack_parser.add_argument("--output", default=None)
    attack_parser.set_defaults(handler=cmd_attack)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = args.handler
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
