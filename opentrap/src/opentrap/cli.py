from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
DEFAULT_DATASET_DIR = DEFAULT_STATE_DIR / "dataset"
DEFAULT_ADAPTER_ENTRYPOINT = DEFAULT_REPO_ROOT / "adapter" / "main.py"
SESSION_START_TIMEOUT_SECONDS = 10.0
SESSION_POLL_INTERVAL_SECONDS = 0.1
CACHE_WAIT_TIMEOUT_SECONDS = 2.0
CACHE_WAIT_POLL_INTERVAL_SECONDS = 0.05
DATASET_FINGERPRINT_VERSION = "v1"


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


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256_hex_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_hex_text(payload: str) -> str:
    return _sha256_hex_bytes(payload.encode("utf-8"))


def _build_dataset_fingerprint(
    trap_id: str,
    shared: SharedConfig,
    trap_config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    sample_records = [
        {"path": sample.path, "content_sha256": _sha256_hex_text(sample.content)}
        for sample in sorted(shared.samples, key=lambda item: item.path)
    ]

    fingerprint_payload: dict[str, Any] = {
        "version": DATASET_FINGERPRINT_VERSION,
        "trap_id": trap_id,
        "shared": {
            "scenario": shared.scenario,
            "content_style": shared.content_style,
            "trap_intent": shared.trap_intent,
            "seed": shared.seed,
        },
        "trap_config": trap_config,
        "samples": sample_records,
    }
    fingerprint = _sha256_hex_bytes(_canonical_json_bytes(fingerprint_payload))
    return fingerprint, fingerprint_payload


def _dataset_cache_dir(trap_id: str, fingerprint: str) -> Path:
    trap_segments = trap_id.split("/")
    return DEFAULT_DATASET_DIR.joinpath(*trap_segments, fingerprint)


def _normalize_data_items(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        path = item.get("path")
        if not isinstance(item_id, str) or not isinstance(path, str):
            continue
        normalized.append({"id": item_id, "path": path})
    return normalized


def _extract_data_items(artifact_path: Path) -> list[dict[str, str]]:
    if artifact_path.is_file():
        return []

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


def _read_cached_dataset_snapshot(cache_dir: Path) -> dict[str, Any] | None:
    cache_metadata_path = cache_dir / "cache.json"
    artifact_path = cache_dir / "artifact"

    if not cache_metadata_path.exists() or not artifact_path.exists():
        return None

    cache_metadata = _load_json_maybe(cache_metadata_path)
    if cache_metadata is None:
        return None

    data_items = _normalize_data_items(cache_metadata.get("data_items"))
    if not data_items:
        data_items = _extract_data_items(artifact_path)

    return {
        "artifact_path": str(artifact_path),
        "metadata_path": str(artifact_path / "metadata.jsonl"),
        "data_dir": str(artifact_path / "data"),
        "data_items": data_items,
    }


def _wait_for_cached_dataset_snapshot(cache_dir: Path) -> dict[str, Any] | None:
    deadline = time.monotonic() + CACHE_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = _read_cached_dataset_snapshot(cache_dir)
        if snapshot is not None:
            return snapshot
        time.sleep(CACHE_WAIT_POLL_INTERVAL_SECONDS)
    return None


def _resolve_cached_dataset(
    trap_id: str,
    trap_slug: str,
    shared: SharedConfig,
    trap_config: dict[str, Any],
    registry: dict[str, TrapSpec],
) -> dict[str, Any]:
    fingerprint, fingerprint_payload = _build_dataset_fingerprint(trap_id, shared, trap_config)
    cache_dir = _dataset_cache_dir(trap_id, fingerprint)

    cached_snapshot = _read_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is not None:
        return {
            "dataset_fingerprint": fingerprint,
            "dataset_cache_dir": str(cache_dir),
            "dataset_source": "cache_hit",
            **cached_snapshot,
        }

    tmp_root = DEFAULT_DATASET_DIR / "_tmp" / uuid.uuid4().hex
    output_base = tmp_root / "output" / trap_slug
    staging_dir = tmp_root / "staging"
    staging_dir.mkdir(parents=True, exist_ok=False)

    published = False
    try:
        generated_artifact = registry[trap_id].run(shared, trap_config, output_base)
        staged_artifact = staging_dir / "artifact"
        generated_artifact.replace(staged_artifact)

        dataset_items = _extract_data_items(staged_artifact)
        cache_payload = {
            "version": DATASET_FINGERPRINT_VERSION,
            "trap_id": trap_id,
            "dataset_fingerprint": fingerprint,
            "created_at_utc": _utc_now_iso(),
            "fingerprint_payload": fingerprint_payload,
            "data_items": dataset_items,
        }
        _write_json(staging_dir / "cache.json", cache_payload)

        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging_dir.replace(cache_dir)
            published = True
        except FileExistsError:
            published = False
    finally:
        if not published:
            shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)

    cached_snapshot = _read_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is None:
        cached_snapshot = _wait_for_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is None:
        raise RuntimeError(f"cached dataset is unavailable at {cache_dir}")

    return {
        "dataset_fingerprint": fingerprint,
        "dataset_cache_dir": str(cache_dir),
        "dataset_source": "generated_then_cached" if published else "cache_hit",
        **cached_snapshot,
    }


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
        trap_intent=_prompt_non_empty("Trap intent: "),
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


def cmd_trap(args: argparse.Namespace) -> int:
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
        dataset = _resolve_cached_dataset(
            trap_id=resolved,
            trap_slug=trap_slug,
            shared=loaded.shared,
            trap_config=loaded.trap_configs[resolved],
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"trap run '{resolved}' failed: {exc}", file=sys.stderr)
        return 1

    trap_entry = {
        "trap_id": resolved,
        "trap_slug": trap_slug,
        "dataset_fingerprint": dataset["dataset_fingerprint"],
        "dataset_cache_dir": dataset["dataset_cache_dir"],
        "dataset_source": dataset["dataset_source"],
        "artifact_path": dataset["artifact_path"],
        "metadata_path": dataset["metadata_path"],
        "data_dir": dataset["data_dir"],
        "data_items": dataset["data_items"],
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
