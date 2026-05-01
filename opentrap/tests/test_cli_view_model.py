from __future__ import annotations

from pathlib import Path

from opentrap.cli_rendering.display_state import RunDisplayState, SecuritySummary
from opentrap.cli_rendering.view_model import artifact_rows, build_run_view_model, evaluation_rows


def test_build_run_view_model_contains_phase_rows_and_counts() -> None:
    state = RunDisplayState(
        trap_id="reasoning/chain-trap",
        target="acme-client",
        run_dir="runs/abc",
        harness_command="bun test",
        generation_status="completed",
        generation_message="Dataset generated",
        adapter_status="completed",
        adapter_message="Adapter ready",
        harness_status="running",
        harness_message="Progress: 1 / 2",
        evaluation_status="running",
        evaluation_message="Evaluation 1/2",
        scenario_cases=2,
        base_cases=1,
        variant_cases=1,
        selected_cases=2,
        harness_executed=1,
        harness_passed=1,
        harness_failed=0,
        scored_cases=1,
        trap_successes=0,
        trap_success_rate="0.0%",
        trap_outcome="secure",
        report_path="runs/abc/evaluation.csv",
    )

    model = build_run_view_model(state)

    assert model.config_rows[0] == ("Trap", "reasoning/chain-trap")
    assert model.steps[2].message == "Progress: 1 / 2"
    assert model.cases_rows == [
        ("Scenario cases", "2"),
        ("  Base", "1"),
        ("  Variants", "1"),
        ("Selected", "2"),
    ]
    assert ("Progress", "1 / 2") in model.execution_rows
    assert ("Outcome", "secure") in model.evaluation_rows


def test_build_run_view_model_stage_specific_panels_and_max_cases() -> None:
    execute_state = RunDisplayState(
        stage="execute",
        trap_id="reasoning/chain-trap",
        target="acme-client",
        run_dir="runs/abc",
        max_cases=3,
        adapter_status="running",
        adapter_message="Launching adapter",
        harness_status="pending",
        harness_message="Harness pending",
    )
    execute_model = build_run_view_model(execute_state)
    assert execute_model.title == "OpenTrap Execute"
    assert ("Max cases", "3") in execute_model.config_rows
    assert execute_model.show_cases_panel is True
    assert execute_model.show_execution_panel is True
    assert execute_model.show_evaluation_panel is False
    assert len(execute_model.steps) == 2

    eval_state = RunDisplayState(
        stage="eval",
        trap_id="reasoning/chain-trap",
        target="acme-client",
        run_dir="runs/abc",
        evaluation_status="running",
        evaluation_message="Evaluating results",
    )
    eval_model = build_run_view_model(eval_state)
    assert eval_model.title == "OpenTrap Eval"
    assert eval_model.show_cases_panel is False
    assert eval_model.show_execution_panel is False
    assert eval_model.show_evaluation_panel is True
    assert len(eval_model.steps) == 1


def test_evaluation_rows_for_unavailable_zero_scored() -> None:
    summary = SecuritySummary(
        run_id="run-1",
        generated_artifacts=1,
        scenario_cases=2,
        base_cases=1,
        variant_cases=1,
        selected_cases=2,
        harness_executed=2,
        harness_passed=2,
        harness_failed=0,
        scored_cases=0,
        trap_successes=0,
        security_status="unavailable",
        display_status="secure",
        rate_percent="0.0%",
        report_path="runs/run-1/evaluation.csv",
    )

    rows = evaluation_rows(summary)

    assert rows == [
        ("Scored cases", "0"),
        ("Trap successes", "0 / 0"),
        ("Success rate", "0.0%"),
        ("Outcome", "secure"),
        ("Report", "runs/run-1/evaluation.csv"),
    ]


def test_artifact_rows_returns_manifest_sessions_and_traces(tmp_path: Path) -> None:
    run_manifest_path = tmp_path / "runs" / "abc" / "run.json"
    run_manifest_path.parent.mkdir(parents=True)
    run_manifest_path.write_text("{}", encoding="utf-8")

    rows = artifact_rows(run_manifest_path)

    assert rows[0][0] == "Run manifest"
    assert rows[1][0] == "Sessions"
    assert rows[2][0] == "Traces"
