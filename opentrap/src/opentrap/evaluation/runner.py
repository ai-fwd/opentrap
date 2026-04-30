"""Shared trap evaluation runner and finalized-run lookup."""

from __future__ import annotations

import datetime
import io
import logging
import sys
from collections.abc import Mapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from opentrap.evaluation.result import EvaluationResult
from opentrap.events import EventSink, emit_event
from opentrap.io_utils import load_json, load_json_maybe, write_json
from opentrap.report import SecurityResult
from opentrap.trap import TrapSpec


def run_trap_evaluation(
    *,
    trap_id: str,
    trap: TrapSpec[Any, Any, Any, Any],
    run_manifest_path: Path,
    event_sink: EventSink,
) -> None:
    emit_event(
        event_sink,
        "evaluate_started",
        trap_id=trap_id,
        run_manifest_path=str(run_manifest_path),
    )
    set_scorer_status(run_manifest_path=run_manifest_path, scorer_status="running")
    report_path = run_manifest_path.parent / "report.json"
    captured_output = _CapturedEvaluationOutput()
    event_sink_for_evaluation = _build_uncaptured_event_sink(event_sink)
    try:
        try:
            with _capture_evaluation_output(captured_output):
                raw_result = trap.evaluate(
                    {
                        "trap_id": trap_id,
                        "run_manifest_path": str(run_manifest_path),
                        "run_dir": str(run_manifest_path.parent),
                        "report_path": str(report_path),
                        "event_sink": event_sink_for_evaluation,
                    }
                )
        finally:
            _emit_captured_evaluation_output(
                event_sink=event_sink,
                trap_id=trap_id,
                run_manifest_path=run_manifest_path,
                captured=captured_output,
            )
        result = _require_trap_eval_result(raw_result)
        _set_security_result(
            run_manifest_path=run_manifest_path,
            security_result=SecurityResult.from_counts(
                success_count=result.success_count,
                evaluated_count=result.evaluated_count,
                details=result.details,
            ),
        )
    except Exception:
        set_scorer_status(run_manifest_path=run_manifest_path, scorer_status="failed")
        _set_security_result(
            run_manifest_path=run_manifest_path,
            security_result=SecurityResult.unavailable(),
        )
        raise
    set_scorer_status(run_manifest_path=run_manifest_path, scorer_status="completed")
    emit_event(
        event_sink,
        "evaluate_completed",
        trap_id=trap_id,
        run_manifest_path=str(run_manifest_path),
    )


class _CapturedEvaluationOutput:
    def __init__(self) -> None:
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()


def _build_uncaptured_event_sink(event_sink: EventSink) -> EventSink:
    stdout = sys.stdout
    stderr = sys.stderr

    def _sink(event: Any) -> None:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            event_sink(event)

    return _sink


@contextmanager
def _capture_evaluation_output(captured: _CapturedEvaluationOutput) -> Any:
    with (
        _redirect_logging_streams(captured.stderr),
        redirect_stdout(captured.stdout),
        redirect_stderr(captured.stderr),
    ):
        yield captured


def _emit_captured_evaluation_output(
    *,
    event_sink: EventSink,
    trap_id: str,
    run_manifest_path: Path,
    captured: _CapturedEvaluationOutput,
) -> None:
    stdout = captured.stdout.getvalue()
    stderr = captured.stderr.getvalue()
    if stdout or stderr:
        emit_event(
            event_sink,
            "evaluation_output",
            trap_id=trap_id,
            run_manifest_path=str(run_manifest_path),
            stdout=stdout,
            stderr=stderr,
        )


@contextmanager
def _redirect_logging_streams(
    stream: io.StringIO,
) -> Any:
    replacements: list[tuple[logging.StreamHandler[Any], Any]] = []
    for handler in _iter_stdout_stderr_logging_handlers():
        replacements.append((handler, handler.stream))
        handler.setStream(stream)
    try:
        yield
    finally:
        for handler, original_stream in reversed(replacements):
            handler.setStream(original_stream)


def _iter_stdout_stderr_logging_handlers() -> list[logging.StreamHandler[Any]]:
    handlers: list[logging.StreamHandler[Any]] = []
    seen: set[int] = set()
    for logger in _iter_known_loggers():
        for handler in logger.handlers:
            if id(handler) in seen:
                continue
            if _is_stdout_stderr_stream_handler(handler):
                seen.add(id(handler))
                handlers.append(handler)
    return handlers


def _iter_known_loggers() -> list[logging.Logger]:
    loggers = [logging.getLogger()]
    for candidate in logging.root.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            loggers.append(candidate)
    return loggers


def _is_stdout_stderr_stream_handler(handler: logging.Handler) -> bool:
    if isinstance(handler, logging.FileHandler):
        return False
    if not isinstance(handler, logging.StreamHandler):
        return False
    return handler.stream in {sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__}


def set_scorer_status(*, run_manifest_path: Path, scorer_status: str) -> None:
    manifest = load_json_maybe(run_manifest_path)
    if manifest is not None:
        manifest["scorer_status"] = scorer_status
        write_json(run_manifest_path, manifest, atomic=True)

    report_path = run_manifest_path.parent / "report.json"
    report = load_json_maybe(report_path)
    if report is not None:
        report["scorer_status"] = scorer_status
        write_json(report_path, report, atomic=True)


def _require_trap_eval_result(value: Any) -> EvaluationResult:
    if not isinstance(value, EvaluationResult):
        raise RuntimeError(
            "trap.evaluate(...) must return EvaluationResult "
            "(success_count, evaluated_count, details)"
        )
    value.validate()
    return value


def _set_security_result(*, run_manifest_path: Path, security_result: SecurityResult) -> None:
    report_path = run_manifest_path.parent / "report.json"
    report = load_json_maybe(report_path) or _build_minimal_report(run_manifest_path)
    report["security_result"] = security_result.to_report_payload()
    write_json(report_path, report, atomic=True)


def _build_minimal_report(run_manifest_path: Path) -> dict[str, Any]:
    manifest = load_json_maybe(run_manifest_path) or {}
    return {
        "run_id": manifest.get("run_id"),
    }


def find_latest_finalized_run_manifest(*, runs_dir: Path, trap_id: str) -> Path:
    if not runs_dir.exists() or not runs_dir.is_dir():
        raise RuntimeError(
            "No finalized run found for trap "
            f"'{trap_id}' (runs directory does not exist: {runs_dir})"
        )

    latest: tuple[datetime.datetime, datetime.datetime, str, Path] | None = None
    for candidate_dir in sorted(runs_dir.iterdir()):
        if not candidate_dir.is_dir():
            continue
        manifest_path = candidate_dir / "run.json"
        if not manifest_path.exists():
            continue
        try:
            payload = load_json(manifest_path)
        except Exception:  # noqa: BLE001
            continue
        if payload.get("status") != "finalized":
            continue
        if not _manifest_includes_trap(payload, trap_id):
            continue
        finalized = _parse_iso_timestamp(payload.get("finalized_at_utc"))
        created = _parse_iso_timestamp(payload.get("created_at_utc"))
        if finalized is None:
            finalized = created
        if finalized is None or created is None:
            continue
        key = (finalized, created, candidate_dir.name, manifest_path)
        if latest is None or key > latest:
            latest = key

    if latest is None:
        raise RuntimeError(f"No finalized run found for trap '{trap_id}' in {runs_dir}")
    return latest[3]


def _parse_iso_timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


def _manifest_includes_trap(payload: Mapping[str, object], trap_id: str) -> bool:
    raw_traps = payload.get("traps")
    if not isinstance(raw_traps, list):
        return False
    for trap_entry in raw_traps:
        if isinstance(trap_entry, dict) and trap_entry.get("trap_id") == trap_id:
            return True
    return False
