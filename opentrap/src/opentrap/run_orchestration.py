"""Trap-run orchestration for dataset resolution, case looping, and harness execution."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from opentrap.config_loader import HarnessConfig
from opentrap.dataset_cache import resolve_cached_dataset
from opentrap.evaluation import run_trap_evaluation
from opentrap.execution_context import (
    ActiveSessionDescriptor,
    active_session_path_for_run,
    clear_active_session_descriptor,
    load_active_session_descriptor,
    write_active_session_descriptor,
)
from opentrap.io_utils import (
    append_jsonl,
    load_json,
    load_json_maybe,
    load_jsonl,
    utc_now_iso,
    write_json,
    write_jsonl,
)
from opentrap.trap_contract import SharedConfig, TrapSpec

ADAPTER_HOST = "127.0.0.1"
ADAPTER_PORT = 7860  # default port so it's easier for PUT changes
ADAPTER_READY_TIMEOUT_SECONDS = 10.0
ADAPTER_POLL_INTERVAL_SECONDS = 0.1
STATUS_HEARTBEAT_INTERVAL_SECONDS = 3.0
ADAPTER_TERMINATE_TIMEOUT_SECONDS = 3.0
SESSIONS_FILE_NAME = "sessions.jsonl"
TRACES_FILE_NAME = "traces.jsonl"

StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class RunEnvironment:
    repo_root: Path
    runs_dir: Path
    dataset_dir: Path
    adapter_generated_root: Path


@dataclass(frozen=True)
class TrapRunResult:
    run_manifest_path: Path
    succeeded: bool


def _launch_adapter(
    manifest_path: Path,
    *,
    environment: RunEnvironment,
    product_under_test: str,
    port: int,
) -> subprocess.Popen[Any]:
    generated_dir = environment.adapter_generated_root / product_under_test
    if not generated_dir.exists() or not generated_dir.is_dir():
        raise RuntimeError("generated adapter output was not found at " f"{generated_dir}")

    command = [
        sys.executable,
        "-m",
        "opentrap.adapter",
        "--manifest",
        str(manifest_path),
        "--host",
        ADAPTER_HOST,
        "--port",
        str(port),
    ]
    return subprocess.Popen(command, cwd=environment.repo_root)


def _wait_for_adapter_ready(
    process: subprocess.Popen[Any],
    *,
    port: int,
    heartbeat_interval_seconds: float = STATUS_HEARTBEAT_INTERVAL_SECONDS,
    on_wait_heartbeat: Callable[[float], None] | None = None,
) -> None:
    started = time.monotonic()
    deadline = started + ADAPTER_READY_TIMEOUT_SECONDS
    next_heartbeat = started + heartbeat_interval_seconds
    health_url = f"http://{ADAPTER_HOST}:{port}/__opentrap/health"
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"adapter exited before health check succeeded (exit code {exit_code})"
            )

        try:
            with urlopen(health_url, timeout=0.2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if on_wait_heartbeat is not None and heartbeat_interval_seconds > 0:
            now = time.monotonic()
            if now >= next_heartbeat:
                on_wait_heartbeat(now - started)
                next_heartbeat += heartbeat_interval_seconds

        time.sleep(ADAPTER_POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"timed out waiting for adapter health ({last_error})")


def _active_session_path(manifest_path: Path) -> Path:
    return active_session_path_for_run(manifest_path.parent)


def _resolve_sessions_file_path(run_dir: Path, manifest: Mapping[str, Any]) -> Path:
    sessions_file = manifest.get("sessions_file")
    if isinstance(sessions_file, str) and sessions_file.strip():
        sessions_path = Path(sessions_file)
    else:
        sessions_path = Path(SESSIONS_FILE_NAME)
    if not sessions_path.is_absolute():
        sessions_path = run_dir / sessions_path
    return sessions_path


def _load_session_payloads_from_manifest(
    manifest: Mapping[str, Any],
    *,
    run_dir: Path,
) -> list[dict[str, Any]]:
    return load_jsonl(_resolve_sessions_file_path(run_dir, manifest))


def _update_session_payload(
    *,
    sessions_path: Path,
    session_id: str,
    updates: Mapping[str, Any],
) -> None:
    payloads = load_jsonl(sessions_path)
    for payload in payloads:
        if payload.get("session_id") == session_id:
            payload.update(dict(updates))
            write_jsonl(sessions_path, payloads, atomic=True)
            return
    raise RuntimeError(f"session_id {session_id!r} was not found in {sessions_path}")


def _start_case_session(manifest_path: Path, *, case_index: int) -> ActiveSessionDescriptor:
    manifest = load_json(manifest_path)
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

    traps = manifest.get("traps")
    if not isinstance(traps, list) or not traps or not isinstance(traps[0], dict):
        raise RuntimeError("manifest.traps must contain the selected trap entry")

    cases = traps[0].get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("manifest.traps[0].cases must be a list")
    if case_index < 0 or case_index >= len(cases):
        raise RuntimeError(f"case index {case_index} is out of range for this run")

    case = cases[case_index]
    if not isinstance(case, dict):
        raise RuntimeError(f"case {case_index} is not a JSON object")
    item_id = case.get("item_id")
    item_id_value = item_id if isinstance(item_id, str) else None

    run_dir = manifest_path.parent
    session_id = uuid.uuid4().hex
    session_path = _resolve_sessions_file_path(run_dir, manifest)
    evidence_path = run_dir / TRACES_FILE_NAME
    started_at_utc = utc_now_iso()

    descriptor = ActiveSessionDescriptor(
        run_id=run_id,
        session_id=session_id,
        case_index=case_index,
        session_path=session_path,
        evidence_path=evidence_path,
        case=dict(case),
    )

    session_payload = {
        "run_id": run_id,
        "session_id": session_id,
        "case_index": case_index,
        "item_id": item_id_value,
        "started_at_utc": started_at_utc,
        "ended_at_utc": None,
        "harness_exit_code": None,
    }
    append_jsonl(session_path, session_payload)
    evidence_path.touch(exist_ok=True)

    sessions = manifest.get("sessions")
    if not isinstance(sessions, list):
        sessions = []
    sessions.append(
        {
            "session_id": session_id,
            "case_index": case_index,
            "evidence_file": evidence_path.name,
        }
    )

    manifest["sessions_file"] = SESSIONS_FILE_NAME
    manifest["sessions"] = sessions
    manifest["active_case_index"] = case_index
    manifest["active_session_id"] = session_id
    manifest["status"] = "session_active"
    write_json(manifest_path, manifest, atomic=True)
    write_active_session_descriptor(_active_session_path(manifest_path), descriptor)
    return descriptor


def _end_case_session(manifest_path: Path, *, harness_exit_code: int) -> None:
    active_path = _active_session_path(manifest_path)
    descriptor = load_active_session_descriptor(active_path)
    if descriptor is None:
        raise RuntimeError("active session descriptor was unexpectedly missing at session end")

    ended_at_utc = utc_now_iso()

    _update_session_payload(
        sessions_path=descriptor.session_path,
        session_id=descriptor.session_id,
        updates={
            "ended_at_utc": ended_at_utc,
            "harness_exit_code": harness_exit_code,
        },
    )

    manifest = load_json(manifest_path)
    manifest["active_case_index"] = None
    manifest["active_session_id"] = None
    manifest["status"] = "ready"
    write_json(manifest_path, manifest, atomic=True)
    clear_active_session_descriptor(active_path)


def _finalize_run(manifest_path: Path, *, succeeded: bool) -> None:
    manifest = load_json(manifest_path)
    ended_at_utc = utc_now_iso()
    run_dir = manifest_path.parent
    traps = manifest.get("traps", [])
    trap_ids = [
        trap_entry["trap_id"]
        for trap_entry in traps
        if isinstance(trap_entry, dict) and isinstance(trap_entry.get("trap_id"), str)
    ]
    session_payloads = _load_session_payloads_from_manifest(manifest, run_dir=run_dir)
    session_count = len(session_payloads)
    failed_session_count = len(
        [
            session
            for session in session_payloads
            if session.get("harness_exit_code") not in {None, 0}
        ]
    )

    manifest["active_case_index"] = None
    manifest["active_session_id"] = None
    manifest["status"] = "finalized"
    manifest["finalized_at_utc"] = ended_at_utc
    manifest["succeeded"] = succeeded
    manifest["scorer_status"] = "pending"

    report_path = manifest_path.parent / "report.json"
    report_payload = {
        "run_id": manifest["run_id"],
        "finalized_at_utc": ended_at_utc,
        "succeeded": succeeded,
        "scorer_status": "pending",
        "trap_count": len(trap_ids),
        "trap_ids": trap_ids,
        "case_count": manifest.get("case_count", 0),
        "session_count": session_count,
        "failed_session_count": failed_session_count,
    }
    write_json(report_path, report_payload, atomic=True)
    manifest["report_path"] = str(report_path)
    write_json(manifest_path, manifest, atomic=True)


def _terminate_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=ADAPTER_TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=ADAPTER_TERMINATE_TIMEOUT_SECONDS)


def run_single_trap(
    *,
    trap_id: str,
    requested_trap_ref: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    environment: RunEnvironment,
    product_under_test: str,
    harness: HarnessConfig,
    status_callback: StatusCallback,
    max_cases: int | None = None,
) -> TrapRunResult:
    run_id = uuid.uuid4().hex
    run_dir = environment.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest_path = run_dir / "run.json"
    trap_slug = trap_id.replace("/", "__")

    run_manifest: dict[str, Any] = {
        "run_id": run_id,
        "repo_root": str(environment.repo_root.resolve()),
        "product_under_test": product_under_test,
        "created_at_utc": utc_now_iso(),
        "requested": requested_trap_ref,
        "status": "creating",
        "scorer_status": "pending",
        "active_case_index": None,
        "active_session_id": None,
        "sessions_file": SESSIONS_FILE_NAME,
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
            on_cache_hit=lambda fingerprint: status_callback(
                f"Dataset cache hit: {fingerprint[:12]}"
            ),
            on_cache_miss=lambda: status_callback("Cache miss; generating dataset..."),
            on_generation_heartbeat=lambda elapsed: status_callback(
                f"Generating dataset... still working ({int(elapsed)}s)"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed during dataset generation: {exc}") from exc

    if not dataset.cases:
        raise RuntimeError("dataset generation completed, but no execution cases were produced")

    total_case_count = len(dataset.cases)
    status_callback(f"Dataset ready: {total_case_count} cases")
    case_count_to_run = total_case_count
    if max_cases is not None:
        if max_cases < 1:
            raise RuntimeError("max_cases must be >= 1")
        case_count_to_run = min(total_case_count, max_cases)
        status_callback(
            f"Fast dev run mode: executing {case_count_to_run}/{total_case_count} case(s)"
        )

    trap_entry = {
        "trap_id": trap_id,
        "trap_slug": trap_slug,
        **dataset.as_manifest_fields(),
    }
    run_manifest["traps"] = [trap_entry]
    run_manifest["trap_count"] = 1
    run_manifest["case_count"] = total_case_count
    run_manifest["status"] = "armed"
    write_json(run_manifest_path, run_manifest, atomic=True)

    harness_cwd = environment.repo_root / harness.cwd
    adapter_port = ADAPTER_PORT
    adapter_process: subprocess.Popen[Any] | None = None
    succeeded = True
    try:
        status_callback(f"Launching adapter runtime for product '{product_under_test}'")
        try:
            adapter_process = _launch_adapter(
                run_manifest_path,
                environment=environment,
                product_under_test=product_under_test,
                port=adapter_port,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed during adapter launch: {exc}") from exc

        try:
            _wait_for_adapter_ready(
                adapter_process,
                port=adapter_port,
                heartbeat_interval_seconds=STATUS_HEARTBEAT_INTERVAL_SECONDS,
                on_wait_heartbeat=lambda elapsed: status_callback(
                    f"Waiting for adapter health... ({int(elapsed)}s)"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed during adapter startup: {exc}") from exc

        ready_manifest = load_json_maybe(run_manifest_path) or run_manifest
        ready_manifest["status"] = "ready"
        ready_manifest["adapter_pid"] = adapter_process.pid
        ready_manifest["adapter_port"] = adapter_port
        ready_manifest["ready_at_utc"] = utc_now_iso()
        write_json(run_manifest_path, ready_manifest, atomic=True)
        status_callback("Adapter ready")

        for case_index in range(case_count_to_run):
            status_callback(f"Starting case {case_index + 1}/{case_count_to_run}")
            descriptor = _start_case_session(run_manifest_path, case_index=case_index)
            status_callback(f"Session active: {descriptor.session_id}")

            harness_exit_code = 1
            try:
                result = subprocess.run(
                    list(harness.command),
                    cwd=harness_cwd,
                    check=False,
                )
                harness_exit_code = int(result.returncode)
            finally:
                _end_case_session(run_manifest_path, harness_exit_code=harness_exit_code)

            if harness_exit_code == 0:
                status_callback(f"Case {case_index + 1} completed")
            else:
                succeeded = False
                status_callback(f"Case {case_index + 1} failed (exit code {harness_exit_code})")
    finally:
        clear_active_session_descriptor(_active_session_path(run_manifest_path))
        _terminate_process(adapter_process)

    _finalize_run(run_manifest_path, succeeded=succeeded)
    status_callback("Run finalized")
    trap_for_evaluation = registry.get(trap_id)
    if trap_for_evaluation is None:
        status_callback("Trap evaluation skipped: selected trap instance was unavailable")
    else:
        try:
            run_trap_evaluation(
                trap_id=trap_id,
                trap=trap_for_evaluation,
                run_manifest_path=run_manifest_path,
                status_callback=status_callback,
            )
        except Exception as exc:  # noqa: BLE001
            status_callback(f"Trap evaluation failed: {exc}")
    return TrapRunResult(run_manifest_path=run_manifest_path, succeeded=succeeded)
