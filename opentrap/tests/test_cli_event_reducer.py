from __future__ import annotations

import json
from pathlib import Path

from opentrap.cli_rendering.display_state import RunDisplayState
from opentrap.cli_rendering.event_reducer import reduce_event
from opentrap.events import RunEvent


def test_reduce_event_run_started_hydrates_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "abc"
    run_manifest_path = run_dir / "run.json"
    counts = {
        "generated_artifacts": 1,
        "scenario_cases": 2,
        "base_cases": 1,
        "variant_cases": 1,
        "selected_cases": 2,
        "harness_executed": 0,
        "harness_passed": 0,
        "harness_failed": 0,
        "scored_cases": 0,
        "trap_successes": 0,
        "evaluation_errors": 0,
    }
    event = RunEvent(
        type="run_started",
        payload={
            "trap_id": "reasoning/chain-trap",
            "target": "acme-client",
            "harness_command": "bun test",
            "run_dir": str(run_dir),
            "run_manifest_path": str(run_manifest_path),
            "counts": counts,
        },
    )

    state = RunDisplayState()
    result = reduce_event(state, event)

    assert result.run_started is True
    assert result.start_live is True
    assert state.trap_id == "reasoning/chain-trap"
    assert state.target == "acme-client"
    assert state.harness_command == "bun test"
    assert state.run_manifest_path == str(run_manifest_path)
    assert state.scenario_cases == 2
    assert state.selected_cases == 2


def test_reduce_event_generate_adapter_case_and_finalize_transitions() -> None:
    state = RunDisplayState()

    result = reduce_event(state, RunEvent(type="generate_started", payload={}))
    assert state.generation_status == "running"
    assert result.progress_message == "... Generating dataset"

    result = reduce_event(
        state,
        RunEvent(type="generate_progress", payload={"state": "cache_hit"}),
    )
    assert state.dataset_from_cache is True
    assert state.generation_message == "Dataset cache hit"
    assert result.status_message == "Dataset cache hit"

    counts = {
        "generated_artifacts": 1,
        "scenario_cases": 2,
        "base_cases": 1,
        "variant_cases": 1,
        "selected_cases": 2,
        "harness_executed": 1,
        "harness_passed": 1,
        "harness_failed": 0,
        "scored_cases": 0,
        "trap_successes": 0,
        "evaluation_errors": 0,
    }
    reduce_event(state, RunEvent(type="generate_completed", payload={"counts": counts}))
    assert state.generation_status == "completed"
    assert state.generation_message == "Dataset generated (from cache)"

    reduce_event(
        state,
        RunEvent(type="adapter_launching", payload={"host": "127.0.0.1", "port": 7860}),
    )
    assert state.adapter_status == "running"
    assert state.adapter_message == "Launching adapter (127.0.0.1:7860)"

    reduce_event(
        state,
        RunEvent(type="case_started", payload={"display_case_index": 2, "selected_cases": 2}),
    )
    assert state.harness_status == "running"
    assert state.harness_message == "Progress: 1 / 2"

    result = reduce_event(
        state,
        RunEvent(type="run_finalized", payload={"counts": counts}),
    )
    assert state.harness_status == "completed"
    assert state.harness_message == "Harness completed"
    assert result.progress_message == "✓ Harness completed"


def test_reduce_event_stage_specific_stop_live_behavior() -> None:
    counts = {
        "generated_artifacts": 1,
        "scenario_cases": 2,
        "base_cases": 1,
        "variant_cases": 1,
        "selected_cases": 2,
        "harness_executed": 1,
        "harness_passed": 1,
        "harness_failed": 0,
        "scored_cases": 0,
        "trap_successes": 0,
        "evaluation_errors": 0,
    }
    generate_state = RunDisplayState(stage="generate")
    generate_result = reduce_event(
        generate_state,
        RunEvent(type="generate_completed", payload={"counts": counts}),
    )
    assert generate_result.stop_live is True

    execute_state = RunDisplayState(stage="execute")
    execute_result = reduce_event(
        execute_state,
        RunEvent(type="run_finalized", payload={"counts": counts}),
    )
    assert execute_result.stop_live is True


def test_reduce_event_run_failed_marks_stage_statuses() -> None:
    state = RunDisplayState(adapter_status="running", harness_status="pending")

    reduce_event(state, RunEvent(type="run_failed", payload={"stage": "run", "error": "boom"}))
    assert state.adapter_status == "failed"

    state = RunDisplayState(evaluation_status="running")
    reduce_event(
        state,
        RunEvent(type="run_failed", payload={"stage": "evaluate", "error": "eval boom"}),
    )
    assert state.evaluation_status == "failed"


def test_reduce_event_evaluate_completed_updates_summary_and_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    run_manifest_path = run_dir / "run.json"
    run_manifest_path.write_text("{}", encoding="utf-8")

    _write_report(
        run_dir,
        scored_cases=0,
        trap_successes=0,
        security_status="unavailable",
    )

    state = RunDisplayState()
    result = reduce_event(
        state,
        RunEvent(type="evaluate_completed", payload={"run_manifest_path": str(run_manifest_path)}),
    )

    assert state.evaluation_status == "skipped"
    assert state.evaluation_message == "Evaluation skipped"
    assert result.progress_message == "⚠ Evaluation skipped: no cases were evaluated"
    assert result.final_summary_path == run_manifest_path

    _write_report(
        run_dir,
        scored_cases=2,
        trap_successes=1,
        security_status="vulnerable",
    )
    result = reduce_event(
        state,
        RunEvent(type="evaluate_completed", payload={"run_manifest_path": str(run_manifest_path)}),
    )
    assert state.evaluation_status == "completed"
    assert state.evaluation_message == "Evaluation completed"
    assert state.evaluation_errors == 0
    assert state.trap_success_rate == "50.0%"
    assert state.trap_outcome == "vulnerable"
    assert result.progress_message == "✓ Evaluation completed"


def _write_report(
    run_dir: Path,
    *,
    scored_cases: int,
    trap_successes: int,
    security_status: str,
    evaluation_errors: int = 0,
) -> None:
    payload = {
        "run_id": "run-1",
        "counts": {
            "generated_artifacts": 1,
            "scenario_cases": 2,
            "base_cases": 1,
            "variant_cases": 1,
            "selected_cases": 2,
            "harness_executed": 2,
            "harness_passed": 2,
            "harness_failed": 0,
            "scored_cases": scored_cases,
            "trap_successes": trap_successes,
            "evaluation_errors": evaluation_errors,
        },
        "security_result": {
            "status": security_status,
        },
    }
    (run_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")
