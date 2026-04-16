"""Trap-run orchestration for manifest lifecycle and adapter session startup.

This module coordinates a single trap execution from dataset resolution through
adapter launch, then updates the run manifest as the run becomes armed and ready.
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentrap.dataset_cache import resolve_cached_dataset
from opentrap.io_utils import load_json_maybe, utc_now_iso, write_json
from opentrap.trap_contract import SharedConfig, TrapSpec

SESSION_START_TIMEOUT_SECONDS = 10.0
SESSION_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class RunEnvironment:
    """Filesystem and process entrypoints used to run one trap session."""

    repo_root: Path
    runs_dir: Path
    dataset_dir: Path
    adapter_entrypoint: Path


def _launch_adapter(manifest_path: Path, environment: RunEnvironment) -> subprocess.Popen[Any]:
    """Start the adapter process pointing at the given run manifest.

    Raises:
        RuntimeError: Adapter entrypoint script does not exist.
    """
    if not environment.adapter_entrypoint.exists():
        raise RuntimeError(f"adapter entrypoint was not found at {environment.adapter_entrypoint}")

    command = [
        sys.executable,
        str(environment.adapter_entrypoint),
        "--manifest",
        str(manifest_path),
    ]
    return subprocess.Popen(command, cwd=environment.repo_root)


def _wait_for_session_start(manifest_path: Path, process: subprocess.Popen[Any]) -> str:
    """Wait until runtime sets `active_session_id` in the manifest.

    Raises:
        RuntimeError: Adapter exits early or session start timeout is reached.
    """
    deadline = time.monotonic() + SESSION_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        manifest = load_json_maybe(manifest_path)
        if manifest is not None:
            session_id = manifest.get("active_session_id")
            if isinstance(session_id, str) and session_id:
                return session_id

        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"adapter exited before session start (exit code {exit_code})")
        time.sleep(SESSION_POLL_INTERVAL_SECONDS)

    raise RuntimeError("timed out waiting for adapter session start")


def run_single_trap(
    *,
    trap_id: str,
    requested_trap_ref: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    environment: RunEnvironment,
) -> Path:
    """Run a single trap and return the run manifest path when ready.

    The function creates a run manifest, resolves dataset cache for the trap input,
    launches the adapter, and marks the run ready once a session id is active.
    """
    run_id = uuid.uuid4().hex
    run_dir = environment.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest_path = run_dir / "run.json"
    trap_slug = trap_id.replace("/", "__")

    run_manifest: dict[str, Any] = {
        "run_id": run_id,
        "created_at_utc": utc_now_iso(),
        "requested": requested_trap_ref,
        "status": "creating",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [],
    }
    write_json(run_manifest_path, run_manifest)

    try:
        dataset = resolve_cached_dataset(
            trap_id=trap_id,
            trap_slug=trap_slug,
            shared=shared,
            trap_config=trap_config,
            registry=registry,
            dataset_dir=environment.dataset_dir,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"trap run '{trap_id}' failed: {exc}") from exc

    trap_entry = {
        "trap_id": trap_id,
        "trap_slug": trap_slug,
        **dataset.as_manifest_fields(),
    }
    run_manifest["traps"] = [trap_entry]
    run_manifest["trap_count"] = 1
    run_manifest["status"] = "armed"
    write_json(run_manifest_path, run_manifest)

    process: subprocess.Popen[Any] | None = None
    try:
        process = _launch_adapter(run_manifest_path, environment)
        _wait_for_session_start(run_manifest_path, process)
    except Exception:
        if process is not None and process.poll() is None:
            process.terminate()
        raise

    ready_manifest = load_json_maybe(run_manifest_path) or run_manifest
    ready_manifest["status"] = "ready"
    ready_manifest["adapter_pid"] = process.pid
    ready_manifest["ready_at_utc"] = utc_now_iso()
    write_json(run_manifest_path, ready_manifest)
    return run_manifest_path
