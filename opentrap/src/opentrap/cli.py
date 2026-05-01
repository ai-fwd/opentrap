"""Command-line entrypoint for listing, initializing, and running OpenTrap traps."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import click
import typer

from opentrap.cli_rendering import build_renderer
from opentrap.config_loader import (
    ConfigError,
    HarnessConfig,
    build_initial_trap_config,
    load_trap_config,
    write_trap_config,
)
from opentrap.evaluation import find_latest_finalized_run_manifest_global, run_trap_evaluation
from opentrap.events import emit_event
from opentrap.io_utils import load_json
from opentrap.run_orchestration import (
    RunEnvironment,
    run_execute_trap,
    run_generate_trap,
    run_single_trap,
)
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


def _empty_counts() -> dict[str, int]:
    return {
        "generated_artifacts": 0,
        "scenario_cases": 0,
        "base_cases": 0,
        "variant_cases": 0,
        "selected_cases": 0,
        "harness_executed": 0,
        "harness_passed": 0,
        "harness_failed": 0,
        "scored_cases": 0,
        "trap_successes": 0,
    }


def _counts_from_manifest(manifest: Mapping[str, object]) -> dict[str, int]:
    raw_counts = manifest.get("counts")
    if not isinstance(raw_counts, Mapping):
        return _empty_counts()
    counts = _empty_counts()
    for key in counts:
        value = raw_counts.get(key)
        if isinstance(value, int):
            counts[key] = value
    return counts


def _load_trap_runtime_inputs(
    *,
    trap_ref: str,
    event_sink,
) -> tuple[str, object, object, RunEnvironment] | None:
    """Load registry/config/trap instance for one trap command."""

    registry = _load_registry()
    if registry is None:
        emit_event(event_sink, "run_failed", stage="validate", error="could not load trap registry")
        return None

    try:
        resolved = _resolve_trap_ref(trap_ref)
    except ValueError as exc:
        emit_event(event_sink, "run_failed", stage="validate", error=str(exc))
        return None
    if not registry.has_trap(resolved):
        emit_event(
            event_sink,
            "run_failed",
            stage="validate",
            error=f"trap '{resolved}' was not found",
        )
        return None

    try:
        trap_fields: dict[str, Mapping[str, TrapFieldSpec]] = {
            trap_id: registry.load_trap_fields(trap_id) for trap_id in registry.trap_ids
        }
    except TrapRegistryError as exc:
        emit_event(event_sink, "run_failed", stage="validate", error=str(exc))
        return None

    try:
        loaded = load_trap_config(
            DEFAULT_CONFIG_PATH,
            trap_fields,
            samples_dir=DEFAULT_SAMPLES_DIR,
        )
    except ConfigError as exc:
        emit_event(event_sink, "run_failed", stage="config", error=str(exc))
        return None

    try:
        selected_trap = registry.create_trap(resolved)
    except TrapRegistryError as exc:
        emit_event(event_sink, "run_failed", stage="trap_init", error=str(exc))
        return None

    environment = RunEnvironment(
        repo_root=DEFAULT_REPO_ROOT,
        runs_dir=DEFAULT_RUNS_DIR,
        dataset_dir=DEFAULT_DATASET_DIR,
        adapter_generated_root=DEFAULT_ADAPTER_GENERATED_ROOT,
    )
    return resolved, loaded, selected_trap, environment


def cmd_run(trap_ref: str, *, max_cases: int | None, verbose: bool) -> int:
    """Execute one trap run."""
    event_sink = build_renderer(verbose=verbose)
    loaded_inputs = _load_trap_runtime_inputs(trap_ref=trap_ref, event_sink=event_sink)
    if loaded_inputs is None:
        return 1
    resolved, loaded, selected_trap, environment = loaded_inputs

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
            max_cases=max_cases,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(event_sink, "run_failed", stage="run", error=str(exc))
        return 1

    return 0 if run_ready.succeeded else 1


def cmd_generate(trap_ref: str, *, force: bool, verbose: bool) -> int:
    """Generate/reuse trap dataset without running harness/eval."""
    event_sink = build_renderer(verbose=verbose)
    loaded_inputs = _load_trap_runtime_inputs(trap_ref=trap_ref, event_sink=event_sink)
    if loaded_inputs is None:
        return 1
    resolved, loaded, selected_trap, environment = loaded_inputs

    emit_event(
        event_sink,
        "run_started",
        trap_id=resolved,
        requested_trap_ref=trap_ref,
        target=loaded.product_under_test,
        harness_command=" ".join(loaded.harness.command),
        stage="generate",
        counts=_empty_counts(),
    )
    try:
        prepared = run_generate_trap(
            trap_id=resolved,
            shared=loaded.shared,
            trap_config=loaded.trap_configs[resolved],
            registry={resolved: selected_trap},
            dataset_dir=environment.dataset_dir,
            event_sink=event_sink,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(event_sink, "run_failed", stage="generate", error=str(exc))
        return 1

    source = "cache hit" if prepared.dataset.dataset_source == "cache_hit" else "cache miss"
    print()
    print("Dataset")
    print(f"Source:       {source}")
    print(f"Path:         {prepared.dataset.dataset_cache_dir}")
    print(f"Fingerprint:  {prepared.dataset.dataset_fingerprint}")
    print(f"Case count:   {prepared.total_case_count}")
    return 0


def cmd_execute(trap_ref: str, *, max_cases: int | None, verbose: bool) -> int:
    """Execute harness against cached trap dataset and finalize run artifacts."""
    event_sink = build_renderer(verbose=verbose)
    loaded_inputs = _load_trap_runtime_inputs(trap_ref=trap_ref, event_sink=event_sink)
    if loaded_inputs is None:
        return 1
    resolved, loaded, selected_trap, environment = loaded_inputs

    try:
        run_ready = run_execute_trap(
            trap_id=resolved,
            requested_trap_ref=trap_ref,
            shared=loaded.shared,
            trap_config=loaded.trap_configs[resolved],
            registry={resolved: selected_trap},
            environment=environment,
            product_under_test=loaded.product_under_test,
            harness=loaded.harness,
            event_sink=event_sink,
            max_cases=max_cases,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(event_sink, "run_failed", stage="run", error=str(exc))
        return 1
    return 0 if run_ready.succeeded else 1


def _resolve_eval_manifest_path(run_ref: str) -> Path:
    if run_ref == "latest":
        return find_latest_finalized_run_manifest_global(runs_dir=DEFAULT_RUNS_DIR)
    manifest_path = DEFAULT_RUNS_DIR / run_ref / "run.json"
    if not manifest_path.exists():
        raise RuntimeError(f"run '{run_ref}' was not found in {DEFAULT_RUNS_DIR}")
    return manifest_path


def _resolve_trap_id_from_run_manifest(manifest: Mapping[str, object]) -> str:
    traps = manifest.get("traps")
    if not isinstance(traps, list):
        raise RuntimeError("run manifest is missing traps payload")
    for trap_entry in traps:
        if isinstance(trap_entry, Mapping):
            trap_id = trap_entry.get("trap_id")
            if isinstance(trap_id, str) and trap_id:
                return trap_id
    raise RuntimeError("run manifest does not contain a valid trap_id")


def cmd_eval(run_ref: str, *, max_cases: int | None, verbose: bool) -> int:
    """Evaluate an existing finalized run."""
    event_sink = build_renderer(verbose=verbose)

    registry = _load_registry()
    if registry is None:
        emit_event(event_sink, "run_failed", stage="validate", error="could not load trap registry")
        return 1

    try:
        run_manifest_path = _resolve_eval_manifest_path(run_ref)
        run_manifest = load_json(run_manifest_path)
        if run_manifest.get("status") != "finalized":
            raise RuntimeError(f"run '{run_manifest_path.parent.name}' is not finalized")
        trap_id = _resolve_trap_id_from_run_manifest(run_manifest)
    except Exception as exc:  # noqa: BLE001
        emit_event(event_sink, "run_failed", stage="validate", error=str(exc))
        return 1

    try:
        trap = registry.create_trap(trap_id)
    except TrapRegistryError as exc:
        emit_event(event_sink, "run_failed", stage="trap_init", error=str(exc))
        return 1

    harness_tokens = run_manifest.get("harness_command")
    harness_command = (
        " ".join(token for token in harness_tokens if isinstance(token, str))
        if isinstance(harness_tokens, list)
        else "-"
    )
    target = run_manifest.get("product_under_test")
    target_value = target if isinstance(target, str) and target else "-"
    emit_event(
        event_sink,
        "run_started",
        trap_id=trap_id,
        requested_trap_ref=trap_id,
        target=target_value,
        harness_command=harness_command,
        run_id=run_manifest.get("run_id"),
        run_dir=str(run_manifest_path.parent),
        run_manifest_path=str(run_manifest_path),
        stage="eval",
        max_cases=max_cases,
        counts=_counts_from_manifest(run_manifest),
    )
    try:
        run_trap_evaluation(
            trap_id=trap_id,
            trap=trap,
            run_manifest_path=run_manifest_path,
            event_sink=event_sink,
            max_cases=max_cases,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(
            event_sink,
            "run_failed",
            stage="evaluate",
            error=f"Trap evaluation failed: {exc}",
        )
        return 1
    return 0


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
    max_cases: Annotated[int | None, typer.Option("--max-cases", min=1)] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> int:
    """Run one trap."""
    return cmd_run(
        trap,
        max_cases=max_cases,
        verbose=verbose,
    )


@app.command("generate")
def generate_command(
    trap: Annotated[str, typer.Argument(help="Trap reference in target/name format.")],
    force: Annotated[bool, typer.Option("--force")] = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> int:
    """Generate/reuse trap dataset only."""
    return cmd_generate(
        trap,
        force=force,
        verbose=verbose,
    )


@app.command("execute")
def execute_command(
    trap: Annotated[str, typer.Argument(help="Trap reference in target/name format.")],
    max_cases: Annotated[int | None, typer.Option("--max-cases", min=1)] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> int:
    """Execute harness on cached trap dataset without evaluation."""
    return cmd_execute(
        trap,
        max_cases=max_cases,
        verbose=verbose,
    )


@app.command("eval")
def eval_command(
    run: Annotated[str, typer.Argument(help="Run id or 'latest'.")],
    max_cases: Annotated[int | None, typer.Option("--max-cases", min=1)] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> int:
    """Evaluate one existing finalized run."""
    return cmd_eval(
        run,
        max_cases=max_cases,
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
