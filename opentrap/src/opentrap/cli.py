"""Command-line entrypoint for listing, initializing, and running OpenTrap traps."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import click
import typer

from opentrap.cli_renderers import build_renderer
from opentrap.config_loader import (
    ConfigError,
    HarnessConfig,
    build_initial_trap_config,
    load_trap_config,
    write_trap_config,
)
from opentrap.evaluation import find_latest_finalized_run_manifest, run_trap_evaluation
from opentrap.events import emit_event
from opentrap.io_utils import load_json_maybe
from opentrap.run_orchestration import RunEnvironment, run_single_trap
from opentrap.trap import SharedConfig, TrapFieldSpec
from opentrap.trap.loader import load_registry_from_candidates
from opentrap.trap.registry import TrapRegistry, TrapRegistryError

DEFAULT_TRAPS_DIR = Path(__file__).resolve().parents[1] / "traps"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_STATE_DIR = Path(".opentrap")
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "opentrap.yaml"
DEFAULT_SAMPLES_DIR = DEFAULT_STATE_DIR / "samples"
DEFAULT_DATASET_DIR = DEFAULT_STATE_DIR / "dataset"
DEFAULT_ADAPTER_GENERATED_ROOT = DEFAULT_REPO_ROOT / "adapter" / "generated"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="OpenTrap CLI",
)


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
    """Prompt until a required non-empty string value is provided."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        typer.echo("Value cannot be empty.", err=True)


def _prompt_seed(prompt: str) -> int | None:
    """Prompt for optional deterministic integer seed (blank -> None)."""
    while True:
        value = input(prompt).strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            typer.echo("Seed must be an integer or blank.", err=True)


def _prompt_command(prompt: str) -> tuple[str, ...]:
    """Prompt for a non-empty shell command and tokenize with shlex."""
    while True:
        value = input(prompt).strip()
        if not value:
            typer.echo("Command cannot be empty.", err=True)
            continue
        try:
            command = tuple(shlex.split(value))
        except ValueError as exc:
            typer.echo(f"Command is invalid: {exc}", err=True)
            continue
        if not command:
            typer.echo("Command cannot be empty.", err=True)
            continue
        return command


def _prompt_relative_path(prompt: str) -> str:
    """Prompt for a required relative path used as harness working directory."""
    while True:
        value = input(prompt).strip()
        if not value:
            typer.echo("Path cannot be empty.", err=True)
            continue
        if Path(value).is_absolute():
            typer.echo("Path must be relative.", err=True)
            continue
        return value


def cmd_list(target: str | None) -> int:
    """List available trap ids, optionally filtered by target prefix."""
    registry = _load_registry()
    if registry is None:
        return 1

    traps = list(registry.trap_ids)
    if target:
        prefix = f"{target.strip().lower()}/"
        traps = [trap_id for trap_id in traps if trap_id.lower().startswith(prefix)]

    for trap_id in traps:
        print(trap_id)
    return 0


def cmd_init() -> int:
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
        command=_prompt_command("What command runs your test suite? (e.g. bunx playwright test): "),
        cwd=_prompt_relative_path(
            "Where should this command be run? (relative path, e.g. acme-client): "
        ),
    )

    try:
        trap_fields = {trap_id: registry.load_trap_fields(trap_id) for trap_id in registry.trap_ids}
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


def _run_started_payload_from_manifest(
    *,
    trap_id: str,
    requested_trap_ref: str,
    run_manifest_path: Path,
    mode: str,
) -> dict[str, object]:
    manifest = load_json_maybe(run_manifest_path) or {}
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        run_id = run_manifest_path.parent.name

    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise RuntimeError("run manifest is missing required counts payload")
    for key in ("scenario_cases", "selected_cases"):
        if not isinstance(counts.get(key), int):
            raise RuntimeError(f"run manifest counts must include integer {key}")

    target = manifest.get("product_under_test")
    if not isinstance(target, str) or not target:
        raise RuntimeError("run manifest is missing product_under_test")

    harness_command = manifest.get("harness_command")
    if not isinstance(harness_command, list) or not harness_command:
        raise RuntimeError("run manifest is missing harness_command")
    harness_tokens = [token for token in harness_command if isinstance(token, str) and token]
    if not harness_tokens:
        raise RuntimeError("run manifest harness_command must contain strings")

    payload: dict[str, object] = {
        "trap_id": trap_id,
        "requested_trap_ref": requested_trap_ref,
        "target": target,
        "harness_command": " ".join(harness_tokens),
        "counts": dict(counts),
        "run_id": run_id,
        "run_dir": str(run_manifest_path.parent),
        "run_manifest_path": str(run_manifest_path),
        "mode": mode,
    }
    return payload


def cmd_run(trap_ref: str, *, fast_dev_run: bool, fast_eval_run: bool, verbose: bool) -> int:
    """Execute one trap run, with optional fast dev/eval modes."""
    event_sink = build_renderer(verbose=verbose)

    if fast_dev_run and fast_eval_run:
        print("--fast-dev-run and --fast-eval-run cannot be used together", file=sys.stderr)
        return 2

    registry = _load_registry()
    if registry is None:
        emit_event(event_sink, "run_failed", stage="validate", error="could not load trap registry")
        return 1

    try:
        resolved = _resolve_trap_ref(trap_ref)
    except ValueError as exc:
        emit_event(event_sink, "run_failed", stage="validate", error=str(exc))
        return 1
    if not registry.has_trap(resolved):
        emit_event(
            event_sink,
            "run_failed",
            stage="validate",
            error=f"trap '{resolved}' was not found",
        )
        return 1

    try:
        trap_fields: dict[str, Mapping[str, TrapFieldSpec]] = {
            trap_id: registry.load_trap_fields(trap_id) for trap_id in registry.trap_ids
        }
    except TrapRegistryError as exc:
        emit_event(event_sink, "run_failed", stage="validate", error=str(exc))
        return 1

    try:
        loaded = load_trap_config(
            DEFAULT_CONFIG_PATH,
            trap_fields,
            samples_dir=DEFAULT_SAMPLES_DIR,
        )
    except ConfigError as exc:
        emit_event(event_sink, "run_failed", stage="config", error=str(exc))
        return 1

    try:
        selected_trap = registry.create_trap(resolved)
    except TrapRegistryError as exc:
        emit_event(event_sink, "run_failed", stage="trap_init", error=str(exc))
        return 1

    environment = RunEnvironment(
        repo_root=DEFAULT_REPO_ROOT,
        runs_dir=DEFAULT_RUNS_DIR,
        dataset_dir=DEFAULT_DATASET_DIR,
        adapter_generated_root=DEFAULT_ADAPTER_GENERATED_ROOT,
    )

    if fast_eval_run:
        try:
            latest_run_manifest_path = find_latest_finalized_run_manifest(
                runs_dir=environment.runs_dir,
                trap_id=resolved,
            )
            started_payload = _run_started_payload_from_manifest(
                trap_id=resolved,
                requested_trap_ref=trap_ref,
                run_manifest_path=latest_run_manifest_path,
                mode="fast_eval",
            )
        except Exception as exc:  # noqa: BLE001
            emit_event(event_sink, "run_failed", stage="fast_eval_select", error=str(exc))
            return 1
        emit_event(
            event_sink,
            "run_started",
            **started_payload,
        )

        try:
            run_trap_evaluation(
                trap_id=resolved,
                trap=selected_trap,
                run_manifest_path=latest_run_manifest_path,
                event_sink=event_sink,
            )
        except Exception as exc:  # noqa: BLE001
            emit_event(
                event_sink,
                "run_failed",
                stage="fast_eval",
                error=f"Fast eval run failed: {exc}",
            )
            return 1

        return 0

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
            event_sink=event_sink,
            max_cases=1 if fast_dev_run else None,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(event_sink, "run_failed", stage="run", error=str(exc))
        return 1

    return 0 if run_ready.succeeded else 1


@app.command("list")
def list_command(
    target: Annotated[str, typer.Option("--target")] = "",
) -> int:
    """List trap ids, optionally filtered by target."""
    return cmd_list(target if target else None)


@app.command("init")
def init_command() -> int:
    """Create `.opentrap/opentrap.yaml` and `.opentrap/samples/`."""
    return cmd_init()


@app.command("run")
def run_command(
    trap: Annotated[str, typer.Argument(help="Trap reference in target/name format.")],
    fast_dev_run: Annotated[bool, typer.Option("--fast-dev-run")] = False,
    fast_eval_run: Annotated[bool, typer.Option("--fast-eval-run")] = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> int:
    """Run one trap with optional fast execution/evaluation modes."""
    return cmd_run(
        trap,
        fast_dev_run=fast_dev_run,
        fast_eval_run=fast_eval_run,
        verbose=verbose,
    )


def main(argv: list[str] | None = None) -> int:
    """Invoke the Typer CLI and normalize click exit behavior to integer codes."""
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    try:
        result = app(args=raw_args, prog_name="opentrap", standalone_mode=False)
        return int(result) if isinstance(result, int) else 0
    except typer.Exit as exc:
        code = exc.exit_code
        return int(code) if isinstance(code, int) else 0
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(exc.exit_code)
    except click.Abort:
        print("Aborted!", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
