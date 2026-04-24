"""Command-line entrypoint for listing, initializing, and running OpenTrap traps.

This module keeps user-facing CLI behavior and delegates trap-run internals to
orchestration modules so command parsing and UX remain easy to reason about.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

from opentrap.config_loader import (
    ConfigError,
    HarnessConfig,
    build_initial_trap_config,
    load_trap_config,
    write_trap_config,
)
from opentrap.run_orchestration import RunEnvironment, run_single_trap
from opentrap.trap_contract import SharedConfig, TrapFieldSpec
from opentrap.trap_registry import TrapRegistry, TrapRegistryError
from opentrap.trap_registry_loader import load_registry_from_candidates

DEFAULT_TRAPS_DIR = Path(__file__).resolve().parents[1] / "traps"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_STATE_DIR = Path(".opentrap")
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "opentrap.yaml"
DEFAULT_SAMPLES_DIR = DEFAULT_STATE_DIR / "samples"
DEFAULT_DATASET_DIR = DEFAULT_STATE_DIR / "dataset"
DEFAULT_ADAPTER_GENERATED_ROOT = DEFAULT_REPO_ROOT / "adapter" / "generated"
STATUS_PREFIX = "[opentrap]"


def _status(message: str) -> None:
    """Emit one user-facing status line for trap runs."""
    print(f"{STATUS_PREFIX} {message}", file=sys.stderr)


def _resolve_trap_ref(trap_ref: str) -> str:
    """Normalize and validate `target/name` trap references from CLI input."""
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


def _load_registry() -> TrapRegistry | None:
    """Load trap registry and render discovery/contract errors for CLI callers."""
    try:
        return load_registry_from_candidates((DEFAULT_TRAPS_DIR,))
    except TrapRegistryError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _prompt_non_empty(prompt: str) -> str:
    """Collect a required text prompt value for interactive `init` flow."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty.", file=sys.stderr)


def _prompt_seed(prompt: str) -> int | None:
    """Collect an optional integer seed used for deterministic trap generation."""
    while True:
        value = input(prompt).strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            print("Seed must be an integer or blank.", file=sys.stderr)


def _prompt_command(prompt: str) -> tuple[str, ...]:
    """Collect a required shell-style command and tokenize it safely."""
    while True:
        value = input(prompt).strip()
        if not value:
            print("Command cannot be empty.", file=sys.stderr)
            continue
        try:
            command = tuple(shlex.split(value))
        except ValueError as exc:
            print(f"Command is invalid: {exc}", file=sys.stderr)
            continue
        if not command:
            print("Command cannot be empty.", file=sys.stderr)
            continue
        return command


def _prompt_relative_path(prompt: str) -> str:
    """Collect a required relative path for harness command execution."""
    while True:
        value = input(prompt).strip()
        if not value:
            print("Path cannot be empty.", file=sys.stderr)
            continue
        if Path(value).is_absolute():
            print("Path must be relative.", file=sys.stderr)
            continue
        return value


def cmd_list(args: argparse.Namespace) -> int:
    """List available trap ids, optionally filtered by target prefix."""
    registry = _load_registry()
    if registry is None:
        return 1

    traps = list(registry.trap_ids)
    if args.target:
        prefix = f"{args.target.strip().lower()}/"
        traps = [trap_id for trap_id in traps if trap_id.lower().startswith(prefix)]

    for trap_id in traps:
        print(trap_id)
    return 0


def cmd_init(_: argparse.Namespace) -> int:
    """Initialize default config and samples directory for first trap runs."""
    registry = _load_registry()
    if registry is None:
        return 1

    shared = SharedConfig(
        scenario=_prompt_non_empty("Scenario: "),
        content_style=_prompt_non_empty("Content style: "),
        trap_intent=_prompt_non_empty("Trap intent: "),
        seed=_prompt_seed("Seed (optional integer): "),
    )
    harness = HarnessConfig(
        command=_prompt_command(
            "What command runs your test suite? (e.g. bunx playwright test): "
        ),
        cwd=_prompt_relative_path(
            "Where should this command be run? (relative path, e.g. acme-client): "
        ),
    )

    try:
        trap_fields = {
            trap_id: registry.load_trap_fields(trap_id)
            for trap_id in registry.trap_ids
        }
        payload = build_initial_trap_config(shared, trap_fields, harness)
    except (ConfigError, TrapRegistryError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_trap_config(DEFAULT_CONFIG_PATH, payload)
    DEFAULT_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Created config file: {DEFAULT_CONFIG_PATH}")
    print(f"Created samples directory: {DEFAULT_SAMPLES_DIR}")
    print(
        "Add one or more representative source examples to the samples directory. "
        "Examples are optional; if present, they guide generation style and structure."
    )
    return 0


def cmd_trap(args: argparse.Namespace) -> int:
    """Execute a single trap id and print the resulting run manifest path."""
    trap_ref = args.trap
    _status(f"Starting trap run: {trap_ref}")
    _status("Validating trap id and loading trap registry...")

    registry = _load_registry()
    if registry is None:
        _status("Failed during trap validation: could not load trap registry")
        return 1

    try:
        resolved = _resolve_trap_ref(trap_ref)
    except ValueError as exc:
        _status(f"Failed during trap validation: {exc}")
        return 1
    if not registry.has_trap(resolved):
        _status(f"Failed during trap validation: trap '{resolved}' was not found")
        return 1

    try:
        trap_fields: dict[str, Mapping[str, TrapFieldSpec]] = {
            trap_id: registry.load_trap_fields(trap_id)
            for trap_id in registry.trap_ids
        }
    except TrapRegistryError as exc:
        _status(f"Failed during trap validation: {exc}")
        return 1

    _status(f"Loading config: {DEFAULT_CONFIG_PATH}")
    try:
        loaded = load_trap_config(
            DEFAULT_CONFIG_PATH,
            trap_fields,
            samples_dir=DEFAULT_SAMPLES_DIR,
        )
    except ConfigError as exc:
        _status(f"Failed during config load: {exc}")
        return 1

    try:
        selected_trap = registry.create_trap(resolved)
    except TrapRegistryError as exc:
        _status(f"Failed during trap initialization: {exc}")
        return 1

    environment = RunEnvironment(
        repo_root=DEFAULT_REPO_ROOT,
        runs_dir=DEFAULT_RUNS_DIR,
        dataset_dir=DEFAULT_DATASET_DIR,
        adapter_generated_root=DEFAULT_ADAPTER_GENERATED_ROOT,
    )
    try:
        run_ready = run_single_trap(
            trap_id=resolved,
            requested_trap_ref=trap_ref,
            shared=loaded.shared,
            trap_config=loaded.trap_configs[resolved],
            registry={resolved: selected_trap},
            environment=environment,
            product_under_test=loaded.product_under_test,
            harness=loaded.harness,
            status_callback=_status,
        )
    except Exception as exc:  # noqa: BLE001
        _status(str(exc))
        return 1

    print(str(run_ready.run_manifest_path))
    return 0 if run_ready.succeeded else 1


def build_parser() -> argparse.ArgumentParser:
    """Build command parser for explicit subcommands."""
    parser = argparse.ArgumentParser(prog="opentrap", description="OpenTrap CLI")
    subparsers = parser.add_subparsers(dest="command", required=False)

    list_parser = subparsers.add_parser("list", help="List available traps")
    list_parser.add_argument("--target", default=None)
    list_parser.set_defaults(handler=cmd_list)

    init_parser = subparsers.add_parser("init", help="Create .opentrap/opentrap.yaml")
    init_parser.set_defaults(handler=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute CLI command with backward-compatible single-arg trap shorthand."""
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    if raw_args and raw_args[0] not in {"list", "init", "-h", "--help"}:
        if len(raw_args) != 1:
            print(
                "trap execution accepts exactly one argument: target/name",
                file=sys.stderr,
            )
            return 2
        return cmd_trap(argparse.Namespace(trap=raw_args[0]))

    parser = build_parser()
    args = parser.parse_args(raw_args)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
