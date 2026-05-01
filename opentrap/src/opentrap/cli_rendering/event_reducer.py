"""Reducer that interprets RunEvents into shared display-state mutations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from opentrap.cli_rendering.display_state import (
    RunDisplayState,
    adapter_endpoint,
    display_path,
    int_or_default,
    load_security_summary,
    path_from_payload,
    require_counts_payload,
)
from opentrap.events import RunEvent


@dataclass
class ReduceResult:
    """Renderer-agnostic signals emitted while reducing one event."""

    run_started: bool = False
    start_live: bool = False
    refresh: bool = False
    stop_live: bool = False
    progress_message: str | None = None
    status_message: str | None = None
    adapter_log_message: str | None = None
    harness_output_payload: Mapping[str, object] | None = None
    evaluation_phase_payload: Mapping[str, object] | None = None
    evaluation_progress_payload: Mapping[str, object] | None = None
    evaluation_output_payload: Mapping[str, object] | None = None
    final_summary_path: Path | None = None
    run_failed_error: str | None = None


def reduce_event(state: RunDisplayState, event: RunEvent) -> ReduceResult:
    """Update display state and emit renderer-neutral actions for one run event."""
    event_type = event.type
    payload = event.payload
    result = ReduceResult()

    if event_type == "run_started":
        _on_run_started(state, payload)
        result.run_started = True
        result.start_live = True
        result.refresh = True
        return result

    if event_type == "generate_started":
        state.generation_status = "running"
        state.generation_message = "Generating dataset"
        result.progress_message = "... Generating dataset"
        result.refresh = True
        return result

    if event_type == "generate_progress":
        status_message = _on_generate_progress(state, payload)
        result.status_message = status_message
        result.refresh = True
        return result

    if event_type == "generate_completed":
        counts = require_counts_payload(payload)
        state.generated_artifacts = counts["generated_artifacts"]
        state.scenario_cases = counts["scenario_cases"]
        state.base_cases = counts["base_cases"]
        state.variant_cases = counts["variant_cases"]
        state.selected_cases = counts["selected_cases"]
        source = "(from cache)" if state.dataset_from_cache else ""
        state.generation_status = "completed"
        state.generation_message = f"Dataset generated {source}".strip()
        result.progress_message = f"✓ {state.generation_message}"
        if state.stage == "generate":
            result.stop_live = True
        result.refresh = True
        return result

    if event_type == "adapter_launching":
        state.adapter_status = "running"
        endpoint = adapter_endpoint(payload)
        suffix = f" ({endpoint})" if endpoint else ""
        state.adapter_message = f"Launching adapter{suffix}"
        result.progress_message = f"... {state.adapter_message}"
        result.refresh = True
        return result

    if event_type == "adapter_ready":
        state.adapter_status = "completed"
        endpoint = adapter_endpoint(payload)
        suffix = f" ({endpoint})" if endpoint else ""
        state.adapter_message = f"Adapter ready{suffix}"
        result.progress_message = f"✓ {state.adapter_message}"
        result.refresh = True
        return result

    if event_type == "adapter_status_update":
        message = payload.get("message")
        if isinstance(message, str):
            result.status_message = f"Adapter: {message}"
        return result

    if event_type == "adapter_log":
        message = payload.get("message")
        if isinstance(message, str):
            result.adapter_log_message = message
        return result

    if event_type == "case_started":
        state.harness_status = "running"
        index = int_or_default(payload.get("display_case_index"), default=0)
        total = int_or_default(payload.get("selected_cases"), default=0)
        state.harness_message = f"Progress: {max(0, index - 1)} / {total}"
        result.refresh = True
        return result

    if event_type == "harness_output":
        result.harness_output_payload = payload
        return result

    if event_type == "case_finished":
        state.harness_executed = int_or_default(payload.get("harness_executed"), default=0)
        state.harness_passed = int_or_default(payload.get("harness_passed"), default=0)
        state.harness_failed = int_or_default(payload.get("harness_failed"), default=0)
        selected = int_or_default(payload.get("selected_cases"), default=state.selected_cases)
        state.selected_cases = selected
        state.harness_message = f"Progress: {state.harness_executed} / {selected}"
        result.refresh = True
        return result

    if event_type == "run_finalized":
        counts = require_counts_payload(payload)
        state.harness_executed = counts["harness_executed"]
        state.harness_passed = counts["harness_passed"]
        state.harness_failed = counts["harness_failed"]
        if state.harness_failed:
            state.harness_status = "failed"
            state.harness_message = "Harness completed with failures"
            result.progress_message = "✗ Harness completed with failures"
        else:
            state.harness_status = "completed"
            state.harness_message = "Harness completed"
            result.progress_message = "✓ Harness completed"
        if state.stage == "execute":
            result.stop_live = True
        result.refresh = True
        return result

    if event_type == "evaluate_started":
        state.evaluation_status = "running"
        state.evaluation_message = "Evaluating results"
        result.progress_message = "... Evaluating results"
        result.refresh = True
        return result

    if event_type == "evaluate_phase":
        phase = payload.get("phase")
        if isinstance(phase, str) and phase:
            state.evaluation_message = f"Evaluation: {phase}"
            result.evaluation_phase_payload = payload
        result.refresh = True
        return result

    if event_type == "evaluate_progress":
        processed = int_or_default(payload.get("processed"), default=0)
        total = int_or_default(payload.get("total"), default=0)
        if total > 0:
            state.evaluation_message = f"Evaluation {processed}/{total}"
            result.evaluation_progress_payload = payload
        result.refresh = True
        return result

    if event_type == "evaluation_output":
        result.evaluation_output_payload = payload
        result.refresh = True
        return result

    if event_type == "evaluate_completed":
        run_manifest_path = path_from_payload(payload, "run_manifest_path")
        if run_manifest_path is None:
            return result
        summary = load_security_summary(run_manifest_path)
        state.scored_cases = summary.scored_cases
        state.trap_successes = summary.trap_successes
        state.evaluation_errors = summary.evaluation_errors
        state.trap_outcome = summary.display_status
        state.trap_success_rate = summary.rate_percent
        state.report_path = summary.report_path
        if summary.security_status == "unavailable" and summary.scored_cases == 0:
            state.evaluation_status = "skipped"
            state.evaluation_message = "Evaluation skipped"
            result.progress_message = "⚠ Evaluation skipped: no cases were evaluated"
        else:
            state.evaluation_status = "completed"
            state.evaluation_message = "Evaluation completed"
            result.progress_message = "✓ Evaluation completed"
        result.final_summary_path = run_manifest_path
        result.refresh = True
        result.stop_live = True
        return result

    if event_type == "run_failed":
        stage = payload.get("stage")
        if stage == "evaluate":
            state.evaluation_status = "failed"
        elif stage == "run":
            if state.adapter_status == "running":
                state.adapter_status = "failed"
            elif state.harness_status == "running":
                state.harness_status = "failed"
        error = payload.get("error")
        if isinstance(error, str) and error:
            result.run_failed_error = error
        result.refresh = True
        result.stop_live = True
        return result

    return result


def _on_generate_progress(state: RunDisplayState, payload: Mapping[str, object]) -> str | None:
    step_state = payload.get("state")
    elapsed = payload.get("elapsed_seconds")
    if step_state == "cache_hit":
        state.dataset_from_cache = True
        state.generation_message = "Dataset cache hit"
        return "Dataset cache hit"
    if step_state == "cache_miss":
        state.dataset_from_cache = False
        return "Dataset cache miss"
    if step_state == "generating" and isinstance(elapsed, int):
        state.generation_message = f"Generating dataset ({elapsed}s)"
        return f"Generating dataset... ({elapsed}s)"
    if step_state == "adapter_wait" and isinstance(elapsed, int):
        state.adapter_message = f"Waiting for adapter health ({elapsed}s)"
        return f"Waiting for adapter health... ({elapsed}s)"
    return None


def _on_run_started(state: RunDisplayState, payload: Mapping[str, object]) -> None:
    stage = payload.get("stage")
    if isinstance(stage, str) and stage:
        state.stage = stage
    trap_id = payload.get("trap_id")
    if isinstance(trap_id, str) and trap_id:
        state.trap_id = trap_id
    target = payload.get("target")
    if isinstance(target, str) and target:
        state.target = target
    harness_command = payload.get("harness_command")
    if isinstance(harness_command, str) and harness_command:
        state.harness_command = harness_command
    run_dir = payload.get("run_dir")
    if isinstance(run_dir, str) and run_dir:
        state.run_dir = display_path(Path(run_dir))
    run_manifest_path = payload.get("run_manifest_path")
    if isinstance(run_manifest_path, str) and run_manifest_path:
        state.run_manifest_path = run_manifest_path
    max_cases = payload.get("max_cases")
    if isinstance(max_cases, int):
        state.max_cases = max_cases
    else:
        state.max_cases = None

    counts_payload = payload.get("counts")
    if isinstance(counts_payload, Mapping):
        counts = require_counts_payload(payload)
        state.generated_artifacts = counts["generated_artifacts"]
        state.scenario_cases = counts["scenario_cases"]
        state.base_cases = counts["base_cases"]
        state.variant_cases = counts["variant_cases"]
        state.selected_cases = counts["selected_cases"]
        state.harness_executed = counts["harness_executed"]
        state.harness_passed = counts["harness_passed"]
        state.harness_failed = counts["harness_failed"]
        state.scored_cases = counts["scored_cases"]
        state.trap_successes = counts["trap_successes"]
        state.evaluation_errors = counts["evaluation_errors"]
