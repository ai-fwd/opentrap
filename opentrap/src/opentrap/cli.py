from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_TRAPS_DIR = Path(__file__).resolve().parents[2] / "traps"
DEFAULT_RUNS_DIR = Path("runs")
TARGETS = ("perception", "reasoning", "memory", "action", "multi-agent")
TARGET_LOOKUP = {name.lower(): name for name in TARGETS}


def _normalize_target(raw: str) -> str:
    normalized = raw.strip().lower()
    target = TARGET_LOOKUP.get(normalized)
    if target is None:
        raise ValueError(f"invalid target '{raw}'")
    return target


def _parse_target_arg(raw: str) -> str:
    try:
        return _normalize_target(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _resolve_trap_ref(trap_ref: str) -> str:
    raw = trap_ref.strip()
    if "/" not in raw:
        raise ValueError("trap must be in 'target/name' format")
    target_raw, trap_name = raw.split("/", 1)
    target = _normalize_target(target_raw)
    if not trap_name:
        raise ValueError("trap name cannot be empty")
    return f"{target}/{trap_name}"


def discover_traps(traps_dir: Path, target: str | None = None) -> list[str]:
    if not traps_dir.exists():
        return []

    indexed_targets = {
        path.name.lower(): path
        for path in traps_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }

    targets = (target,) if target else TARGETS
    discovered: list[str] = []
    for target_name in targets:
        target_dir = indexed_targets.get(target_name.lower())
        if target_dir is None:
            continue
        for path in target_dir.iterdir():
            if path.is_dir() and not path.name.startswith("."):
                discovered.append(f"{target_name}/{path.name}")
    return sorted(discovered)


def build_attack_report(scenario_ids: list[str], requested: str | None) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "requested": requested,
        "scenario_count": len(scenario_ids),
        "scenario_ids": scenario_ids,
        "outcome": "unknown",
        "evidence": [],
    }


def cmd_list(args: argparse.Namespace) -> int:
    traps = discover_traps(DEFAULT_TRAPS_DIR, target=args.target)
    for trap_id in traps:
        print(trap_id)
    return 0


def cmd_attack(args: argparse.Namespace) -> int:
    available = discover_traps(DEFAULT_TRAPS_DIR)
    trap_ref = args.trap

    if trap_ref is None:
        selected = available
        default_output = DEFAULT_RUNS_DIR / "all.json"
    else:
        try:
            resolved = _resolve_trap_ref(trap_ref)
        except ValueError as exc:
            print(
                str(exc),
                file=sys.stderr,
            )
            return 1
        if resolved not in available:
            print(f"trap '{resolved}' was not found", file=sys.stderr)
            return 1
        selected = [resolved]
        default_output = DEFAULT_RUNS_DIR / f"{resolved.replace('/', '__')}.json"

    report = build_attack_report(scenario_ids=selected, requested=trap_ref)
    output_path = Path(args.output) if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opentrap", description="OpenTrap red-team CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List available traps")
    list_parser.add_argument("--target", type=_parse_target_arg, default=None)
    list_parser.set_defaults(handler=cmd_list)

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
