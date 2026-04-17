"""Trap-run orchestration for manifest lifecycle and adapter session startup.

This module coordinates a single trap execution from dataset resolution through
adapter launch, then updates the run manifest as the run becomes armed and ready.
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentrap.dataset_cache import resolve_cached_dataset
from opentrap.io_utils import load_json_maybe, utc_now_iso, write_json
from opentrap.trap_contract import SharedConfig, TrapSpec

SESSION_START_TIMEOUT_SECONDS = 10.0
SESSION_POLL_INTERVAL_SECONDS = 0.1
STATUS_HEARTBEAT_INTERVAL_SECONDS = 3.0

StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class RunEnvironment:
    """Filesystem and process entrypoints used to run one trap session."""

    repo_root: Path
    runs_dir: Path
    dataset_dir: Path
    adapter_entrypoint: Path


@dataclass(frozen=True)
class TrapRunReady:
    """Trap run state when adapter session is active and orchestration is attached."""

    run_manifest_path: Path
    adapter_process: subprocess.Popen[Any]


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


def _wait_for_session_start(
    manifest_path: Path,
    process: subprocess.Popen[Any],
    *,
    heartbeat_interval_seconds: float = STATUS_HEARTBEAT_INTERVAL_SECONDS,
    on_wait_heartbeat: Callable[[float], None] | None = None,
) -> str:
    """Wait until runtime records a session entry in the run manifest.

    Raises:
        RuntimeError: Adapter exits early or session start timeout is reached.
    """
    def _extract_session_id(manifest: Mapping[str, Any]) -> str | None:
        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            return None
        for session in reversed(sessions):
            if not isinstance(session, dict):
                continue
            session_id = session.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None

    started = time.monotonic()
    deadline = started + SESSION_START_TIMEOUT_SECONDS
    next_heartbeat = started + heartbeat_interval_seconds
    while time.monotonic() < deadline:
        manifest = load_json_maybe(manifest_path)
        if manifest is not None:
            session_id = _extract_session_id(manifest)
            if session_id is not None:
                return session_id

        if on_wait_heartbeat is not None and heartbeat_interval_seconds > 0:
            now = time.monotonic()
            if now >= next_heartbeat:
                on_wait_heartbeat(now - started)
                next_heartbeat += heartbeat_interval_seconds

        exit_code = process.poll()
        if exit_code is not None:
            manifest = load_json_maybe(manifest_path)
            if manifest is not None:
                session_id = _extract_session_id(manifest)
                if session_id is not None:
                    return session_id
            raise RuntimeError(f"adapter exited before session start (exit code {exit_code})")
        time.sleep(SESSION_POLL_INTERVAL_SECONDS)

    manifest = load_json_maybe(manifest_path)
    if manifest is not None:
        session_id = _extract_session_id(manifest)
        if session_id is not None:
            return session_id
    raise RuntimeError("timed out waiting for adapter session start")


def run_single_trap(
    *,
    trap_id: str,
    requested_trap_ref: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    environment: RunEnvironment,
    status_callback: StatusCallback,
) -> TrapRunReady:
    """Run a single trap and return attached run state when ready.

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
        "repo_root": str(environment.repo_root.resolve()),
        "created_at_utc": utc_now_iso(),
        "requested": requested_trap_ref,
        "status": "creating",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [],
    }
    write_json(run_manifest_path, run_manifest)

    status_callback("Resolving dataset cache...")
    try:
        dataset = resolve_cached_dataset(
            trap_id=trap_id,
            trap_slug=trap_slug,
            shared=shared,
            trap_config=trap_config,
            registry=registry,
            dataset_dir=environment.dataset_dir,
            heartbeat_interval_seconds=STATUS_HEARTBEAT_INTERVAL_SECONDS,
            on_cache_hit=lambda fingerprint: status_callback(f"Dataset cache hit: {fingerprint[:12]}"),
            on_cache_miss=lambda: status_callback("Cache miss; generating dataset..."),
            on_generation_heartbeat=lambda elapsed: status_callback(
                f"Generating dataset... still working ({int(elapsed)}s)"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed during dataset generation: {exc}") from exc

    status_callback(f"Dataset ready: {len(dataset.data_items)} items")

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
        status_callback(f"Launching adapter: {environment.adapter_entrypoint}")
        try:
            process = _launch_adapter(run_manifest_path, environment)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed during adapter launch: {exc}") from exc

        status_callback("Waiting for adapter session start...")
        try:
            session_id = _wait_for_session_start(
                run_manifest_path,
                process,
                heartbeat_interval_seconds=STATUS_HEARTBEAT_INTERVAL_SECONDS,
                on_wait_heartbeat=lambda elapsed: status_callback(
                    f"Waiting for adapter session start... ({int(elapsed)}s)"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed during adapter session startup: {exc}") from exc
    except Exception:
        if process is not None and process.poll() is None:
            process.terminate()
        raise

    if process is None:
        raise RuntimeError("adapter process handle was unexpectedly missing after startup")

    ready_manifest = load_json_maybe(run_manifest_path) or run_manifest
    if ready_manifest.get("status") == "finalized":
        status_callback(f"Session active: {session_id}")
        status_callback("Run finalized")
        return TrapRunReady(run_manifest_path=run_manifest_path, adapter_process=process)

    status_callback(f"Session active: {session_id}")
    ready_manifest["status"] = "ready"
    ready_manifest["adapter_pid"] = process.pid
    ready_manifest["ready_at_utc"] = utc_now_iso()
    write_json(run_manifest_path, ready_manifest)
    status_callback("Run ready")
    return TrapRunReady(run_manifest_path=run_manifest_path, adapter_process=process)
