"""Trap-run orchestration for dataset resolution, case looping, and harness execution."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from opentrap.artifacts import (
    REPORT_FILE_NAME,
    RUN_MANIFEST_FILE_NAME,
    SESSIONS_FILE_NAME,
    TRACES_FILE_NAME,
)
from opentrap.config_loader import HarnessConfig
from opentrap.counts import COUNT_FIELDS
from opentrap.dataset_cache import DatasetSnapshot, resolve_cached_dataset
from opentrap.evaluation import run_trap_evaluation
from opentrap.events import EventSink, emit_event
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
from opentrap.trap import SharedConfig, TrapCaseContext, TrapSpec

ADAPTER_HOST = "127.0.0.1"
ADAPTER_PORT = 7860  # default port so it's easier for PUT changes
ADAPTER_READY_TIMEOUT_SECONDS = 10.0
ADAPTER_POLL_INTERVAL_SECONDS = 0.1
STATUS_HEARTBEAT_INTERVAL_SECONDS = 3.0
ADAPTER_TERMINATE_TIMEOUT_SECONDS = 3.0
ADAPTER_STATUS_PREFIX = "[adapter]"


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


@dataclass(frozen=True)
class PreparedTrapDataset:
    trap_id: str
    trap_slug: str
    trap_entry: dict[str, Any]
    counts: dict[str, int]
    selected_case_count: int
    total_case_count: int
    dataset: DatasetSnapshot


@dataclass
class _AdapterStderrBridge:
    thread: threading.Thread
    buffered_lines: list[str]


@dataclass(frozen=True)
class _HarnessProgress:
    completed_case_indexes: set[int]
    next_case_index: int
    harness_executed: int
    harness_passed: int
    harness_failed: int


def _initial_counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_FIELDS, 0)


def _counts_from_manifest(manifest: Mapping[str, Any]) -> dict[str, int]:
    raw_counts = manifest.get("counts")
    counts = _initial_counts()
    if not isinstance(raw_counts, Mapping):
        return counts
    for key in counts:
        value = raw_counts.get(key)
        if isinstance(value, int) and value >= 0:
            counts[key] = value
    return counts


def _update_manifest_counts(manifest_path: Path, *, updates: Mapping[str, int]) -> dict[str, int]:
    manifest = load_json(manifest_path)
    raw_counts = manifest.get("counts")
    if not isinstance(raw_counts, dict):
        raw_counts = _initial_counts()
    counts = dict(raw_counts)
    for key, value in updates.items():
        if key not in COUNT_FIELDS:
            raise RuntimeError(f"unknown count key '{key}'")
        if value < 0:
            raise RuntimeError(f"count '{key}' must be >= 0")
        counts[key] = int(value)
    manifest["counts"] = counts
    write_json(manifest_path, manifest, atomic=True)
    return counts


def _clear_run_active_session(manifest_path: Path) -> None:
    manifest = load_json(manifest_path)
    status = manifest.get("status")
    if status == "session_active":
        manifest["status"] = "ready"
    manifest["active_case_index"] = None
    manifest["active_session_id"] = None
    write_json(manifest_path, manifest, atomic=True)
    clear_active_session_descriptor(_active_session_path(manifest_path))


def _compute_harness_progress(
    *,
    manifest_path: Path,
    selected_case_count: int,
) -> _HarnessProgress:
    if selected_case_count < 0:
        raise RuntimeError("selected_case_count must be >= 0")
    manifest = load_json(manifest_path)
    session_payloads = _load_session_payloads_from_manifest(manifest, run_dir=manifest_path.parent)
    completed_by_case_index: dict[int, int] = {}
    for session_payload in session_payloads:
        case_index = session_payload.get("case_index")
        ended_at_utc = session_payload.get("ended_at_utc")
        harness_exit_code = session_payload.get("harness_exit_code")
        if (
            not isinstance(case_index, int)
            or case_index < 0
            or case_index >= selected_case_count
            or not isinstance(ended_at_utc, str)
            or not ended_at_utc
            or not isinstance(harness_exit_code, int)
        ):
            continue
        completed_by_case_index[case_index] = harness_exit_code

    completed_case_indexes = set(completed_by_case_index)
    next_case_index = 0
    while next_case_index < selected_case_count and next_case_index in completed_case_indexes:
        next_case_index += 1

    harness_executed = len(completed_case_indexes)
    harness_failed = sum(1 for exit_code in completed_by_case_index.values() if exit_code != 0)
    harness_passed = harness_executed - harness_failed
    return _HarnessProgress(
        completed_case_indexes=completed_case_indexes,
        next_case_index=next_case_index,
        harness_executed=harness_executed,
        harness_passed=harness_passed,
        harness_failed=harness_failed,
    )


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
    return subprocess.Popen(
        command,
        cwd=environment.repo_root,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _forward_adapter_stderr_line(raw_line: str, *, event_sink: EventSink) -> bool:
    line = raw_line.rstrip("\r\n")
    if not line:
        return False
    if line == ADAPTER_STATUS_PREFIX:
        emit_event(event_sink, "adapter_status_update", message="")
        return True
    prefix = f"{ADAPTER_STATUS_PREFIX} "
    if line.startswith(prefix):
        emit_event(event_sink, "adapter_status_update", message=line[len(prefix) :])
        return True
    return False


def _start_adapter_stderr_bridge(
    process: subprocess.Popen[Any],
    *,
    event_sink: EventSink,
) -> _AdapterStderrBridge | None:
    stderr_stream = process.stderr
    if stderr_stream is None:
        return None

    buffered_lines: list[str] = []

    def _reader() -> None:
        for raw_line in stderr_stream:
            handled = _forward_adapter_stderr_line(raw_line, event_sink=event_sink)
            if handled:
                continue
            line = raw_line.rstrip("\r\n")
            if line:
                buffered_lines.append(line)
                emit_event(event_sink, "adapter_log", message=line)

    thread = threading.Thread(target=_reader, name="opentrap-adapter-stderr", daemon=True)
    thread.start()
    return _AdapterStderrBridge(thread=thread, buffered_lines=buffered_lines)


def _stop_adapter_stderr_bridge(
    bridge: _AdapterStderrBridge | None,
    *,
    process: subprocess.Popen[Any] | None,
) -> list[str]:
    if bridge is None:
        return []
    if process is not None and process.stderr is not None:
        with suppress(OSError):
            process.stderr.close()
    bridge.thread.join(timeout=1.0)
    return list(bridge.buffered_lines)


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


def _start_case_session(
    manifest_path: Path,
    *,
    case_index: int,
    case: Mapping[str, Any],
) -> ActiveSessionDescriptor:
    manifest = load_json(manifest_path)
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

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

    manifest["sessions_file"] = SESSIONS_FILE_NAME
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
    passed_session_count = max(0, session_count - failed_session_count)

    raw_counts = manifest.get("counts")
    if not isinstance(raw_counts, dict):
        raise RuntimeError("run manifest is missing required 'counts' payload")
    counts = dict(raw_counts)
    counts["harness_executed"] = session_count
    counts["harness_passed"] = passed_session_count
    counts["harness_failed"] = failed_session_count

    manifest["active_case_index"] = None
    manifest["active_session_id"] = None
    manifest["status"] = "finalized"
    manifest["finalized_at_utc"] = ended_at_utc
    manifest["succeeded"] = succeeded
    manifest["scorer_status"] = "pending"
    manifest["counts"] = counts

    report_path = manifest_path.parent / REPORT_FILE_NAME
    report_payload = {
        "run_id": manifest["run_id"],
        "finalized_at_utc": ended_at_utc,
        "succeeded": succeeded,
        "scorer_status": "pending",
        "trap_count": len(trap_ids),
        "trap_ids": trap_ids,
        "counts": counts,
        "security_result": {
            "status": "unavailable",
            "trap_success_count": 0,
            "trap_failure_count": 0,
            "evaluated_count": 0,
            "trap_success_rate": None,
            "details": {},
        },
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


def prepare_trap_dataset(
    *,
    trap_id: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    dataset_dir: Path,
    event_sink: EventSink,
    max_cases: int | None = None,
    require_cache: bool = False,
    force: bool = False,
) -> PreparedTrapDataset:
    trap_slug = trap_id.replace("/", "__")
    emit_event(event_sink, "generate_started", trap_id=trap_id)
    try:
        dataset = resolve_cached_dataset(
            trap_id=trap_id,
            trap_slug=trap_slug,
            shared=shared,
            trap_config=trap_config,
            registry=registry,
            dataset_dir=dataset_dir,
            heartbeat_interval_seconds=STATUS_HEARTBEAT_INTERVAL_SECONDS,
            on_cache_hit=lambda fingerprint: emit_event(
                event_sink,
                "generate_progress",
                trap_id=trap_id,
                state="cache_hit",
                fingerprint=fingerprint,
            ),
            on_cache_miss=lambda: emit_event(
                event_sink,
                "generate_progress",
                trap_id=trap_id,
                state="cache_miss",
            ),
            on_generation_heartbeat=lambda elapsed: emit_event(
                event_sink,
                "generate_progress",
                trap_id=trap_id,
                state="generating",
                elapsed_seconds=int(elapsed),
            ),
            require_cache=require_cache,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed during dataset generation: {exc}") from exc

    if not dataset.cases:
        raise RuntimeError("dataset generation completed, but no execution cases were produced")

    total_case_count = len(dataset.cases)
    trap = registry[trap_id]
    generation_counts = trap.generation_counts(
        TrapCaseContext(
            artifact_path=Path(dataset.artifact_path),
            metadata_path=Path(dataset.metadata_path),
            data_dir=Path(dataset.data_dir),
            data_items=tuple(dict(item) for item in dataset.data_items),
        )
    )
    generated_artifact_count = int(generation_counts.generated_artifacts)
    base_case_count = int(generation_counts.base_cases)
    variant_case_count = int(generation_counts.variant_cases)
    if generated_artifact_count != len(dataset.data_items):
        raise RuntimeError(
            "trap generation counts mismatch: generated_artifacts does not match data_items"
        )
    if total_case_count != base_case_count + variant_case_count:
        raise RuntimeError(
            "trap generation counts mismatch: scenario_cases must equal base_cases + variant_cases"
        )

    selected_case_count = total_case_count
    if max_cases is not None:
        if max_cases < 1:
            raise RuntimeError("max_cases must be >= 1")
        selected_case_count = min(total_case_count, max_cases)

    counts = _initial_counts()
    counts["generated_artifacts"] = generated_artifact_count
    counts["scenario_cases"] = total_case_count
    counts["base_cases"] = base_case_count
    counts["variant_cases"] = variant_case_count
    counts["selected_cases"] = selected_case_count
    emit_event(
        event_sink,
        "generate_completed",
        trap_id=trap_id,
        counts=counts,
    )

    trap_entry = {
        "trap_id": trap_id,
        "trap_slug": trap_slug,
        **dataset.as_manifest_fields(),
    }
    trap_entry["selected_case_count"] = selected_case_count
    trap_entry["case_count"] = selected_case_count

    return PreparedTrapDataset(
        trap_id=trap_id,
        trap_slug=trap_slug,
        trap_entry=trap_entry,
        counts=counts,
        selected_case_count=selected_case_count,
        total_case_count=total_case_count,
        dataset=dataset,
    )


def _load_resume_payload(
    *,
    run_manifest_path: Path,
) -> tuple[str, dict[str, Any], HarnessConfig, str, str]:
    manifest = load_json(run_manifest_path)
    status = manifest.get("status")
    if status == "finalized":
        run_id = run_manifest_path.parent.name
        raise RuntimeError(f"run '{run_id}' is already finalized and cannot be continued")
    if status not in {"armed", "ready", "session_active"}:
        raise RuntimeError(
            f"run '{run_manifest_path.parent.name}' is not resumable "
            f"(unexpected status {status!r})"
        )

    traps = manifest.get("traps")
    if not isinstance(traps, list) or not traps or not isinstance(traps[0], dict):
        raise RuntimeError("run manifest is missing traps[0] payload")
    trap_entry = dict(traps[0])
    trap_id = trap_entry.get("trap_id")
    if not isinstance(trap_id, str) or not trap_id:
        raise RuntimeError("run manifest traps[0].trap_id must be a non-empty string")

    raw_command = manifest.get("harness_command")
    if not isinstance(raw_command, list) or not raw_command:
        raise RuntimeError("run manifest harness_command must be a non-empty list")
    command_tokens: list[str] = []
    for index, token in enumerate(raw_command):
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError(f"run manifest harness_command[{index}] must be a non-empty string")
        command_tokens.append(token)

    raw_cwd = manifest.get("harness_cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd.strip():
        raise RuntimeError("run manifest harness_cwd must be a non-empty string")
    harness = HarnessConfig(command=tuple(command_tokens), cwd=raw_cwd)

    product_under_test = manifest.get("product_under_test")
    if not isinstance(product_under_test, str) or not product_under_test:
        raise RuntimeError("run manifest product_under_test must be a non-empty string")

    requested = manifest.get("requested")
    requested_trap_ref = requested if isinstance(requested, str) and requested else trap_id

    return trap_id, trap_entry, harness, product_under_test, requested_trap_ref


def _extract_data_items_from_metadata(
    *,
    metadata_path: Path,
    data_dir: Path,
) -> list[dict[str, str]]:
    data_items: list[dict[str, str]] = []
    for raw_line in metadata_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        if not isinstance(record, dict):
            continue
        item_id = record.get("file_id")
        filename = record.get("filename")
        if isinstance(item_id, str) and isinstance(filename, str):
            data_items.append({"id": item_id, "path": str(data_dir / filename)})
    return data_items


def _normalize_cases(raw_cases: object) -> list[dict[str, Any]]:
    if not isinstance(raw_cases, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(raw_cases):
        if not isinstance(case, dict):
            continue
        case_payload = dict(case)
        case_payload["case_index"] = index
        normalized.append(case_payload)
    return normalized


def _build_dataset_snapshot_from_trap_entry(
    trap_entry: Mapping[str, Any],
    *,
    trap: TrapSpec[Any, Any, Any, Any],
) -> DatasetSnapshot:
    artifact_path = Path(str(trap_entry.get("artifact_path", "")))
    metadata_path = Path(str(trap_entry.get("metadata_path", "")))
    data_dir = Path(str(trap_entry.get("data_dir", "")))
    data_items = _extract_data_items_from_metadata(metadata_path=metadata_path, data_dir=data_dir)
    context = TrapCaseContext(
        artifact_path=artifact_path,
        metadata_path=metadata_path,
        data_dir=data_dir,
        data_items=tuple(dict(item) for item in data_items),
    )
    cases = _normalize_cases(trap.build_cases(context))

    return DatasetSnapshot(
        dataset_fingerprint=str(trap_entry.get("dataset_fingerprint", "")),
        dataset_cache_dir=str(trap_entry.get("dataset_cache_dir", "")),
        dataset_source=str(trap_entry.get("dataset_source", "cache_hit")),
        artifact_path=str(artifact_path),
        metadata_path=str(metadata_path),
        data_dir=str(data_dir),
        data_items=data_items,
        cases=cases,
    )


def execute_prepared_trap(
    *,
    prepared: PreparedTrapDataset,
    requested_trap_ref: str,
    environment: RunEnvironment,
    product_under_test: str,
    harness: HarnessConfig,
    event_sink: EventSink,
    stage: str,
    max_cases: int | None,
    run_id: str | None = None,
    run_dir: Path | None = None,
    run_manifest_path: Path | None = None,
    emit_run_started_event: bool = True,
    initialize_manifest: bool = True,
    resume_from_case_index: int = 0,
    completed_case_indexes: set[int] | None = None,
    starting_counts: Mapping[str, int] | None = None,
) -> TrapRunResult:
    resolved_run_id = run_id or uuid.uuid4().hex
    resolved_run_dir = run_dir or (environment.runs_dir / resolved_run_id)
    resolved_manifest_path = run_manifest_path or (resolved_run_dir / RUN_MANIFEST_FILE_NAME)

    if run_dir is None and initialize_manifest:
        resolved_run_dir.mkdir(parents=True, exist_ok=False)

    if resume_from_case_index < 0:
        raise RuntimeError("resume_from_case_index must be >= 0")
    completed_indexes = (
        set(completed_case_indexes)
        if completed_case_indexes is not None
        else set(range(max(0, resume_from_case_index)))
    )

    counts_for_run_started = dict(prepared.counts)
    if starting_counts is not None:
        for key in ("harness_executed", "harness_passed", "harness_failed"):
            value = starting_counts.get(key)
            if isinstance(value, int) and value >= 0:
                counts_for_run_started[key] = value

    if emit_run_started_event:
        emit_event(
            event_sink,
            "run_started",
            trap_id=prepared.trap_id,
            requested_trap_ref=requested_trap_ref,
            target=product_under_test,
            harness_command=" ".join(harness.command),
            run_id=resolved_run_id,
            run_dir=str(resolved_run_dir),
            run_manifest_path=str(resolved_manifest_path),
            stage=stage,
            max_cases=max_cases,
            counts=counts_for_run_started,
        )

    if initialize_manifest:
        run_manifest: dict[str, Any] = {
            "run_id": resolved_run_id,
            "repo_root": str(environment.repo_root.resolve()),
            "product_under_test": product_under_test,
            "created_at_utc": utc_now_iso(),
            "requested": requested_trap_ref,
            "status": "armed",
            "run_mode": "run" if stage == "run" else stage,
            "scorer_status": "pending",
            "active_case_index": None,
            "active_session_id": None,
            "harness_command": list(harness.command),
            "harness_cwd": harness.cwd,
            "sessions_file": SESSIONS_FILE_NAME,
            "traps": [prepared.trap_entry],
            "trap_count": 1,
            "counts": counts_for_run_started,
        }
        write_json(resolved_manifest_path, run_manifest, atomic=True)
    else:
        run_manifest = load_json_maybe(resolved_manifest_path) or {}

    harness_cwd = environment.repo_root / harness.cwd
    adapter_port = ADAPTER_PORT
    adapter_process: subprocess.Popen[Any] | None = None
    adapter_stderr_bridge: _AdapterStderrBridge | None = None
    harness_executed = int(counts_for_run_started.get("harness_executed", 0))
    harness_passed = int(counts_for_run_started.get("harness_passed", 0))
    harness_failed = int(counts_for_run_started.get("harness_failed", 0))
    succeeded = harness_failed == 0

    has_remaining_cases = any(
        case_index not in completed_indexes
        for case_index in range(resume_from_case_index, prepared.selected_case_count)
    )
    try:
        if has_remaining_cases:
            emit_event(
                event_sink,
                "adapter_launching",
                product_under_test=product_under_test,
                host=ADAPTER_HOST,
                port=adapter_port,
            )
            try:
                adapter_process = _launch_adapter(
                    resolved_manifest_path,
                    environment=environment,
                    product_under_test=product_under_test,
                    port=adapter_port,
                )
                adapter_stderr_bridge = _start_adapter_stderr_bridge(
                    adapter_process,
                    event_sink=event_sink,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Failed during adapter launch: {exc}") from exc

            try:
                _wait_for_adapter_ready(
                    adapter_process,
                    port=adapter_port,
                    heartbeat_interval_seconds=STATUS_HEARTBEAT_INTERVAL_SECONDS,
                    on_wait_heartbeat=lambda elapsed: emit_event(
                        event_sink,
                        "generate_progress",
                        trap_id=prepared.trap_id,
                        state="adapter_wait",
                        elapsed_seconds=int(elapsed),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Failed during adapter startup: {exc}") from exc

            ready_manifest = load_json_maybe(resolved_manifest_path) or run_manifest
            ready_manifest["status"] = "ready"
            ready_manifest["adapter_pid"] = adapter_process.pid
            ready_manifest["adapter_port"] = adapter_port
            ready_manifest["ready_at_utc"] = utc_now_iso()
            write_json(resolved_manifest_path, ready_manifest, atomic=True)
            emit_event(event_sink, "adapter_ready", host=ADAPTER_HOST, port=adapter_port)

            for case_index in range(resume_from_case_index, prepared.selected_case_count):
                if case_index in completed_indexes:
                    continue
                emit_event(
                    event_sink,
                    "case_started",
                    case_index=case_index,
                    display_case_index=case_index + 1,
                    selected_cases=prepared.selected_case_count,
                )
                case = prepared.dataset.cases[case_index]
                descriptor = _start_case_session(
                    resolved_manifest_path,
                    case_index=case_index,
                    case=case,
                )

                harness_exit_code = 1
                harness_stdout = ""
                harness_stderr = ""
                try:
                    result = subprocess.run(
                        list(harness.command),
                        cwd=harness_cwd,
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    harness_exit_code = int(result.returncode)
                    harness_stdout = result.stdout or ""
                    harness_stderr = result.stderr or ""
                finally:
                    _end_case_session(resolved_manifest_path, harness_exit_code=harness_exit_code)

                emit_event(
                    event_sink,
                    "harness_output",
                    case_index=case_index,
                    display_case_index=case_index + 1,
                    selected_cases=prepared.selected_case_count,
                    exit_code=harness_exit_code,
                    session_id=descriptor.session_id,
                    stdout=harness_stdout,
                    stderr=harness_stderr,
                )

                harness_executed += 1
                if harness_exit_code == 0:
                    harness_passed += 1
                    emit_event(
                        event_sink,
                        "case_finished",
                        case_index=case_index,
                        display_case_index=case_index + 1,
                        selected_cases=prepared.selected_case_count,
                        harness_executed=harness_executed,
                        harness_passed=harness_passed,
                        harness_failed=harness_failed,
                        exit_code=harness_exit_code,
                        session_id=descriptor.session_id,
                        succeeded=True,
                    )
                else:
                    succeeded = False
                    harness_failed += 1
                    emit_event(
                        event_sink,
                        "case_finished",
                        case_index=case_index,
                        display_case_index=case_index + 1,
                        selected_cases=prepared.selected_case_count,
                        harness_executed=harness_executed,
                        harness_passed=harness_passed,
                        harness_failed=harness_failed,
                        exit_code=harness_exit_code,
                        session_id=descriptor.session_id,
                        succeeded=False,
                    )
                _update_manifest_counts(
                    resolved_manifest_path,
                    updates={
                        "harness_executed": harness_executed,
                        "harness_passed": harness_passed,
                        "harness_failed": harness_failed,
                    },
                )
    finally:
        clear_active_session_descriptor(_active_session_path(resolved_manifest_path))
        _terminate_process(adapter_process)
        _stop_adapter_stderr_bridge(adapter_stderr_bridge, process=adapter_process)

    _finalize_run(resolved_manifest_path, succeeded=succeeded)
    emit_event(
        event_sink,
        "run_finalized",
        run_manifest_path=str(resolved_manifest_path),
        succeeded=succeeded,
        counts=load_json(resolved_manifest_path)["counts"],
    )
    return TrapRunResult(run_manifest_path=resolved_manifest_path, succeeded=succeeded)


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
    event_sink: EventSink,
    max_cases: int | None = None,
) -> TrapRunResult:
    run_id = uuid.uuid4().hex
    run_dir = environment.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest_path = run_dir / RUN_MANIFEST_FILE_NAME
    emit_event(
        event_sink,
        "run_started",
        trap_id=trap_id,
        requested_trap_ref=requested_trap_ref,
        target=product_under_test,
        harness_command=" ".join(harness.command),
        run_id=run_id,
        run_dir=str(run_dir),
        run_manifest_path=str(run_manifest_path),
        stage="run",
        max_cases=max_cases,
        counts=_initial_counts(),
    )
    prepared = prepare_trap_dataset(
        trap_id=trap_id,
        shared=shared,
        trap_config=trap_config,
        registry=registry,
        dataset_dir=environment.dataset_dir,
        event_sink=event_sink,
        max_cases=max_cases,
    )
    run_ready = execute_prepared_trap(
        prepared=prepared,
        requested_trap_ref=requested_trap_ref,
        environment=environment,
        product_under_test=product_under_test,
        harness=harness,
        event_sink=event_sink,
        stage="run",
        max_cases=max_cases,
        run_id=run_id,
        run_dir=run_dir,
        run_manifest_path=run_manifest_path,
        emit_run_started_event=False,
    )
    trap_for_evaluation = registry.get(trap_id)
    if trap_for_evaluation is not None:
        try:
            run_trap_evaluation(
                trap_id=trap_id,
                trap=trap_for_evaluation,
                run_manifest_path=run_ready.run_manifest_path,
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
    return run_ready


def run_generate_trap(
    *,
    trap_id: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    dataset_dir: Path,
    event_sink: EventSink,
    force: bool = False,
) -> PreparedTrapDataset:
    return prepare_trap_dataset(
        trap_id=trap_id,
        shared=shared,
        trap_config=trap_config,
        registry=registry,
        dataset_dir=dataset_dir,
        event_sink=event_sink,
        force=force,
    )


def run_execute_trap(
    *,
    trap_id: str,
    requested_trap_ref: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    environment: RunEnvironment,
    product_under_test: str,
    harness: HarnessConfig,
    event_sink: EventSink,
    max_cases: int | None = None,
) -> TrapRunResult:
    run_id = uuid.uuid4().hex
    run_dir = environment.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest_path = run_dir / RUN_MANIFEST_FILE_NAME
    emit_event(
        event_sink,
        "run_started",
        trap_id=trap_id,
        requested_trap_ref=requested_trap_ref,
        target=product_under_test,
        harness_command=" ".join(harness.command),
        run_id=run_id,
        run_dir=str(run_dir),
        run_manifest_path=str(run_manifest_path),
        stage="execute",
        max_cases=max_cases,
        counts=_initial_counts(),
    )
    prepared = prepare_trap_dataset(
        trap_id=trap_id,
        shared=shared,
        trap_config=trap_config,
        registry=registry,
        dataset_dir=environment.dataset_dir,
        event_sink=event_sink,
        max_cases=max_cases,
        require_cache=True,
    )
    return execute_prepared_trap(
        prepared=prepared,
        requested_trap_ref=requested_trap_ref,
        environment=environment,
        product_under_test=product_under_test,
        harness=harness,
        event_sink=event_sink,
        stage="execute",
        max_cases=max_cases,
        run_id=run_id,
        run_dir=run_dir,
        run_manifest_path=run_manifest_path,
        emit_run_started_event=False,
    )


def run_continue_trap(
    *,
    run_manifest_path: Path,
    trap: TrapSpec[Any, Any, Any, Any],
    environment: RunEnvironment,
    event_sink: EventSink,
) -> TrapRunResult:
    trap_id, trap_entry, harness, product_under_test, requested_trap_ref = _load_resume_payload(
        run_manifest_path=run_manifest_path
    )
    counts = _counts_from_manifest(load_json(run_manifest_path))
    selected_case_count = counts["selected_cases"]

    progress = _compute_harness_progress(
        manifest_path=run_manifest_path,
        selected_case_count=selected_case_count,
    )
    _clear_run_active_session(run_manifest_path)
    counts["selected_cases"] = selected_case_count
    counts["harness_executed"] = progress.harness_executed
    counts["harness_passed"] = progress.harness_passed
    counts["harness_failed"] = progress.harness_failed
    _update_manifest_counts(
        run_manifest_path,
        updates={
            "selected_cases": selected_case_count,
            "harness_executed": progress.harness_executed,
            "harness_passed": progress.harness_passed,
            "harness_failed": progress.harness_failed,
        },
    )

    manifest = load_json(run_manifest_path)
    if manifest.get("run_mode") is None:
        manifest["run_mode"] = "run"
        write_json(run_manifest_path, manifest, atomic=True)

    run_ready = execute_prepared_trap(
        prepared=PreparedTrapDataset(
            trap_id=trap_id,
            trap_slug=str(trap_entry.get("trap_slug", trap_id.replace("/", "__"))),
            trap_entry=trap_entry,
            counts=counts,
            selected_case_count=selected_case_count,
            total_case_count=selected_case_count,
            dataset=_build_dataset_snapshot_from_trap_entry(trap_entry, trap=trap),
        ),
        requested_trap_ref=requested_trap_ref,
        environment=environment,
        product_under_test=product_under_test,
        harness=harness,
        event_sink=event_sink,
        stage="run",
        max_cases=None,
        run_id=run_manifest_path.parent.name,
        run_dir=run_manifest_path.parent,
        run_manifest_path=run_manifest_path,
        emit_run_started_event=True,
        initialize_manifest=False,
        resume_from_case_index=progress.next_case_index,
        completed_case_indexes=progress.completed_case_indexes,
        starting_counts=counts,
    )
    try:
        run_trap_evaluation(
            trap_id=trap_id,
            trap=trap,
            run_manifest_path=run_ready.run_manifest_path,
            event_sink=event_sink,
            max_cases=None,
        )
    except Exception as exc:  # noqa: BLE001
        emit_event(
            event_sink,
            "run_failed",
            stage="evaluate",
            error=f"Trap evaluation failed: {exc}",
        )
    return run_ready
