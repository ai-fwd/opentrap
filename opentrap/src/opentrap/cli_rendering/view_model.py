"""View-model helpers that convert display state into renderer-friendly values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opentrap.cli_rendering.display_state import RunDisplayState, SecuritySummary, display_path


@dataclass(frozen=True)
class StepView:
    status: str
    message: str


@dataclass(frozen=True)
class RunViewModel:
    title: str
    config_rows: list[tuple[str, str]]
    steps: list[StepView]
    show_cases_panel: bool
    show_execution_panel: bool
    show_evaluation_panel: bool
    cases_rows: list[tuple[str, str]]
    execution_rows: list[tuple[str, str]]
    evaluation_rows: list[tuple[str, str]]


@dataclass(frozen=True)
class FinalSummaryView:
    show_cases: bool
    show_execution: bool
    show_evaluation: bool
    cases_rows: list[tuple[str, str]]
    execution_rows: list[tuple[str, str]]
    evaluation_rows: list[tuple[str, str]]


def build_run_view_model(state: RunDisplayState) -> RunViewModel:
    stage = state.stage if state.stage in {"run", "generate", "execute", "eval"} else "run"
    config_rows = [
        ("Trap", state.trap_id),
        ("Target", state.target),
        ("Run", display_path(Path(state.run_dir)) if state.run_dir != "-" else state.run_dir),
    ]
    if state.max_cases is not None:
        config_rows.append(("Max cases", str(state.max_cases)))

    if stage == "generate":
        steps = [StepView(status=state.generation_status, message=state.generation_message)]
    elif stage == "execute":
        steps = [
            StepView(status=state.adapter_status, message=state.adapter_message),
            StepView(status=state.harness_status, message=state.harness_message),
        ]
    elif stage == "eval":
        steps = [StepView(status=state.evaluation_status, message=state.evaluation_message)]
    else:
        steps = [
            StepView(status=state.generation_status, message=state.generation_message),
            StepView(status=state.adapter_status, message=state.adapter_message),
            StepView(status=state.harness_status, message=state.harness_message),
            StepView(status=state.evaluation_status, message=state.evaluation_message),
        ]

    show_cases_panel = stage in {"run", "generate", "execute"}
    show_execution_panel = stage in {"run", "execute"}
    show_evaluation_panel = stage in {"run", "eval"}

    return RunViewModel(
        title=f"OpenTrap {stage.title()}",
        config_rows=config_rows,
        steps=steps,
        show_cases_panel=show_cases_panel,
        show_execution_panel=show_execution_panel,
        show_evaluation_panel=show_evaluation_panel,
        cases_rows=[
            ("Scenario cases", str(state.scenario_cases)),
            ("  Base", str(state.base_cases)),
            ("  Variants", str(state.variant_cases)),
            ("Selected", str(state.selected_cases)),
        ],
        execution_rows=[
            ("Harness", state.harness_command),
            ("Progress", f"{state.harness_executed} / {state.selected_cases}"),
            ("Harness Passed", str(state.harness_passed)),
            ("Harness Failed", str(state.harness_failed)),
        ],
        evaluation_rows=[
            ("Scored cases", str(state.scored_cases)),
            ("Evaluation errors", str(state.evaluation_errors)),
            ("Trap successes", f"{state.trap_successes} / {state.scored_cases}"),
            ("Success rate", state.trap_success_rate),
            ("Outcome", state.trap_outcome),
            ("Report", state.report_path),
        ],
    )


def build_final_summary_view(summary: SecuritySummary) -> FinalSummaryView:
    return FinalSummaryView(
        show_cases=True,
        show_execution=True,
        show_evaluation=True,
        cases_rows=cases_rows(summary),
        execution_rows=execution_rows(summary),
        evaluation_rows=evaluation_rows(summary),
    )


def cases_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    return [
        ("Scenario cases", str(summary.scenario_cases)),
        ("  Base", str(summary.base_cases)),
        ("  Variants", str(summary.variant_cases)),
        ("Selected", str(summary.selected_cases)),
    ]


def execution_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    return [
        ("Progress", f"{summary.harness_executed} / {summary.selected_cases}"),
        ("Harness Passed", str(summary.harness_passed)),
        ("Harness Failed", str(summary.harness_failed)),
    ]


def evaluation_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    rows = [
        ("Scored cases", str(summary.scored_cases)),
        ("Evaluation errors", str(summary.evaluation_errors)),
    ]
    if summary.security_status == "unavailable" and summary.scored_cases == 0:
        rows.append(("Trap successes", "0 / 0"))
        rows.append(("Success rate", "0.0%"))
        rows.append(("Outcome", "secure"))
    else:
        rows.append(("Trap successes", f"{summary.trap_successes} / {summary.scored_cases}"))
        rows.append(("Success rate", summary.rate_percent))
        rows.append(("Outcome", summary.display_status))
    rows.append(("Report", summary.report_path))
    return rows


def artifact_rows(run_manifest_path: Path) -> list[tuple[str, str]]:
    return [
        ("Run manifest", display_path(run_manifest_path)),
        ("Sessions", display_path(run_manifest_path.parent / "sessions.jsonl")),
        ("Traces", display_path(run_manifest_path.parent / "traces.jsonl")),
    ]


def step_style(status: str) -> tuple[str, str, str]:
    """Return symbol, color name, and rich symbol for one step status."""
    if status == "running":
        return ("…", "cyan", "spinner")
    if status == "completed":
        return ("✓", "green", "check")
    if status == "failed":
        return ("✗", "red", "error")
    if status == "skipped":
        return ("i", "blue", "info")
    return ("-", "dim", "pending")
