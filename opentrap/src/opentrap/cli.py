from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_STATE_DIR = Path(".opentrap")
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "opentrap.yaml"
DEFAULT_SAMPLES_DIR = DEFAULT_STATE_DIR / "samples"
DEFAULT_ADAPTER_ENTRYPOINT = DEFAULT_REPO_ROOT / "adapter" / "main.py"
SESSION_START_TIMEOUT_SECONDS = 10.0
SESSION_POLL_INTERVAL_SECONDS = 0.1


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


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return raw


def _load_json_maybe(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _extract_data_items(artifact_path: Path) -> list[dict[str, str]]:
    metadata_path = artifact_path / "metadata.jsonl"
    data_dir = artifact_path / "data"
    collected: list[dict[str, str]] = []

    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            item_id = record.get("file_id")
            filename = record.get("filename")
            if not isinstance(item_id, str) or not isinstance(filename, str):
                continue
            item_path = data_dir / filename
            collected.append({"id": item_id, "path": str(item_path)})
        if collected:
            return collected

    if data_dir.exists():
        for html_path in sorted(path for path in data_dir.iterdir() if path.is_file()):
            collected.append({"id": html_path.stem, "path": str(html_path)})

    return collected


def _launch_adapter(manifest_path: Path) -> subprocess.Popen[Any]:
    if not DEFAULT_ADAPTER_ENTRYPOINT.exists():
        raise RuntimeError(f"adapter entrypoint was not found at {DEFAULT_ADAPTER_ENTRYPOINT}")

    command = [
        sys.executable,
        str(DEFAULT_ADAPTER_ENTRYPOINT),
        "--manifest",
        str(manifest_path),
    ]
    return subprocess.Popen(command, cwd=DEFAULT_REPO_ROOT)


def _wait_for_session_start(manifest_path: Path, process: subprocess.Popen[Any]) -> str:
    deadline = time.monotonic() + SESSION_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        manifest = _load_json_maybe(manifest_path)
        if manifest is not None:
            session_id = manifest.get("active_session_id")
            if isinstance(session_id, str) and session_id:
                return session_id

        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"adapter exited before session start (exit code {exit_code})")
        time.sleep(SESSION_POLL_INTERVAL_SECONDS)

    raise RuntimeError("timed out waiting for adapter session start")


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
        trap_intent=_prompt_non_empty("Attack intent: "),
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

    trap_ref = args.trap
    try:
        resolved = _resolve_trap_ref(trap_ref)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if resolved not in registry:
        print(f"trap '{resolved}' was not found", file=sys.stderr)
        return 1

    try:
        loaded = load_attack_config(
            DEFAULT_CONFIG_PATH,
            registry,
            samples_dir=DEFAULT_SAMPLES_DIR,
        )
    except AttackConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex
    run_dir = DEFAULT_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest_path = run_dir / "run.json"
    trap_slug = resolved.replace("/", "__")

    run_manifest: dict[str, Any] = {
        "run_id": run_id,
        "created_at_utc": _utc_now_iso(),
        "requested": trap_ref,
        "status": "creating",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [],
    }
    _write_json(run_manifest_path, run_manifest)

    try:
        artifact_path = registry[resolved].run(
            loaded.shared,
            loaded.trap_configs[resolved],
            run_dir / trap_slug,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"trap '{resolved}' failed: {exc}", file=sys.stderr)
        return 1

    trap_entry = {
        "trap_id": resolved,
        "trap_slug": trap_slug,
        "artifact_path": str(artifact_path),
        "metadata_path": str(artifact_path / "metadata.jsonl"),
        "data_dir": str(artifact_path / "data"),
        "data_items": _extract_data_items(artifact_path),
    }
    run_manifest["traps"] = [trap_entry]
    run_manifest["trap_count"] = 1
    run_manifest["status"] = "armed"
    _write_json(run_manifest_path, run_manifest)

    process: subprocess.Popen[Any] | None = None
    try:
        process = _launch_adapter(run_manifest_path)
        _wait_for_session_start(run_manifest_path, process)
    except Exception as exc:  # noqa: BLE001
        if process is not None and process.poll() is None:
            process.terminate()
        print(str(exc), file=sys.stderr)
        return 1

    ready_manifest = _load_json(run_manifest_path)
    ready_manifest["status"] = "ready"
    ready_manifest["adapter_pid"] = process.pid
    ready_manifest["ready_at_utc"] = _utc_now_iso()
    _write_json(run_manifest_path, ready_manifest)

    print(str(run_manifest_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opentrap", description="OpenTrap red-team CLI")
    subparsers = parser.add_subparsers(dest="command", required=False)

    list_parser = subparsers.add_parser("list", help="List available traps")
    list_parser.add_argument("--target", default=None)
    list_parser.set_defaults(handler=cmd_list)

    init_parser = subparsers.add_parser("init", help="Create .opentrap/opentrap.yaml")
    init_parser.set_defaults(handler=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    if raw_args and raw_args[0] not in {"list", "init", "-h", "--help"}:
        if len(raw_args) != 1:
            print(
                "trap execution accepts exactly one argument: target/name",
                file=sys.stderr,
            )
            return 2
        return cmd_attack(argparse.Namespace(trap=raw_args[0]))

    parser = build_parser()
    args = parser.parse_args(raw_args)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
