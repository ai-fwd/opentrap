"""Renderers for OpenTrap CLI lifecycle events."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from opentrap.events import EventSink, RunEvent
from opentrap.io_utils import load_json_maybe

STATUS_PREFIX = "[opentrap]"
VERBOSE_BUFFER_LIMIT = 300


@dataclass(frozen=True)
class SecuritySummary:
    """Normalized report fields used by CLI final summary renderers."""

    run_id: str
    generated_artifacts: int
    scenario_cases: int
    base_cases: int
    variant_cases: int
    selected_cases: int
    harness_executed: int
    harness_passed: int
    harness_failed: int
    scored_cases: int
    trap_successes: int
    security_status: str
    display_status: str
    rate_percent: str
    report_path: str


@dataclass
class _RunDisplayState:
    trap_id: str = "-"
    target: str = "-"
    harness_command: str = "-"
    run_dir: str = "-"
    run_manifest_path: str = ""
    generated_artifacts: int = 0
    scenario_cases: int = 0
    base_cases: int = 0
    variant_cases: int = 0
    selected_cases: int = 0
    harness_executed: int = 0
    dataset_from_cache: bool = False
    generation_status: str = "pending"
    generation_message: str = "Dataset pending"
    adapter_status: str = "pending"
    adapter_message: str = "Adapter pending"
    harness_status: str = "pending"
    harness_message: str = "Harness pending"
    harness_passed: int = 0
    harness_failed: int = 0
    scored_cases: int = 0
    trap_successes: int = 0
    trap_outcome: str = "pending"
    trap_success_rate: str = "0.0%"
    report_path: str = "-"
    evaluation_status: str = "pending"
    evaluation_message: str = "Evaluation pending"


class PlainRenderer:
    """Deterministic line renderer for non-TTY environments."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._state = _RunDisplayState()
        self._run_header_printed = False

    def __call__(self, event: RunEvent) -> None:
        event_type = event.type
        payload = event.payload

        if event_type == "run_started":
            self._on_run_started(payload)
            return

        if event_type == "generate_started":
            self._state.generation_status = "running"
            self._state.generation_message = "Generating dataset"
            self._progress("... Generating dataset")
            return

        if event_type == "generate_progress":
            state = payload.get("state")
            elapsed = payload.get("elapsed_seconds")
            if state == "cache_hit":
                self._state.dataset_from_cache = True
                self._state.generation_message = "Dataset cache hit"
                self._verbose_status("Dataset cache hit")
            elif state == "cache_miss":
                self._state.dataset_from_cache = False
                self._verbose_status("Dataset cache miss")
            elif state == "generating" and isinstance(elapsed, int):
                self._verbose_status(f"Generating dataset... ({elapsed}s)")
            elif state == "adapter_wait" and isinstance(elapsed, int):
                self._verbose_status(f"Waiting for adapter health... ({elapsed}s)")
            return

        if event_type == "generate_completed":
            counts = _require_counts_payload(payload)
            self._state.generated_artifacts = counts["generated_artifacts"]
            self._state.scenario_cases = counts["scenario_cases"]
            self._state.base_cases = counts["base_cases"]
            self._state.variant_cases = counts["variant_cases"]
            self._state.selected_cases = counts["selected_cases"]
            self._state.generation_status = "completed"
            source = "(from cache)" if self._state.dataset_from_cache else ""
            self._state.generation_message = f"Dataset generated {source}".strip()
            self._progress(f"✓ {self._state.generation_message}")
            return

        if event_type == "adapter_launching":
            self._state.adapter_status = "running"
            endpoint = _adapter_endpoint(payload)
            suffix = f" ({endpoint})" if endpoint else ""
            self._state.adapter_message = f"Launching adapter{suffix}"
            self._progress(f"... {self._state.adapter_message}")
            return

        if event_type == "adapter_ready":
            self._state.adapter_status = "completed"
            endpoint = _adapter_endpoint(payload)
            suffix = f" ({endpoint})" if endpoint else ""
            self._state.adapter_message = f"Adapter ready{suffix}"
            self._progress(f"✓ {self._state.adapter_message}")
            return

        if event_type == "adapter_status_update":
            message = payload.get("message")
            if isinstance(message, str):
                self._verbose_status(f"Adapter: {message}")
            return

        if event_type == "adapter_log":
            message = payload.get("message")
            if isinstance(message, str) and self.verbose:
                print(f"Adapter log: {message}", file=sys.stderr)
            return

        if event_type == "case_started":
            self._state.harness_status = "running"
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("selected_cases"), default=0)
            self._state.harness_message = f"Progress: {max(0, index - 1)} / {total}"
            return

        if event_type == "harness_output":
            if self.verbose:
                self._print_harness_output(payload)
            return

        if event_type == "case_finished":
            self._state.harness_executed = _int_or_default(
                payload.get("harness_executed"),
                default=0,
            )
            self._state.harness_passed = _int_or_default(payload.get("harness_passed"), default=0)
            self._state.harness_failed = _int_or_default(payload.get("harness_failed"), default=0)
            selected = _int_or_default(
                payload.get("selected_cases"),
                default=self._state.selected_cases,
            )
            self._state.selected_cases = selected
            self._state.harness_message = f"Progress: {self._state.harness_executed} / {selected}"
            return

        if event_type == "run_finalized":
            counts = _require_counts_payload(payload)
            self._state.harness_executed = counts["harness_executed"]
            self._state.harness_passed = counts["harness_passed"]
            self._state.harness_failed = counts["harness_failed"]
            if self._state.harness_failed:
                self._state.harness_status = "failed"
                self._progress("✗ Harness completed with failures")
            else:
                self._state.harness_status = "completed"
                self._progress("✓ Harness completed")
            return

        if event_type == "evaluate_started":
            self._state.evaluation_status = "running"
            self._state.evaluation_message = "Evaluating results"
            self._progress("... Evaluating results")
            return

        if event_type == "evaluate_phase":
            if self.verbose:
                self._print_evaluation_phase(payload)
            return

        if event_type == "evaluate_progress":
            if self.verbose:
                self._print_evaluation_progress(payload)
            return

        if event_type == "evaluation_output":
            if self.verbose:
                self._print_evaluation_output(payload)
            return

        if event_type == "evaluate_completed":
            run_manifest_path = _path_from_payload(payload, "run_manifest_path")
            if run_manifest_path is None:
                return
            summary = load_security_summary(run_manifest_path)
            self._state.scored_cases = summary.scored_cases
            self._state.trap_successes = summary.trap_successes
            self._state.trap_outcome = summary.display_status
            self._state.trap_success_rate = summary.rate_percent
            self._state.report_path = summary.report_path
            if summary.security_status == "unavailable" and summary.scored_cases == 0:
                self._state.evaluation_status = "skipped"
                self._state.evaluation_message = "Evaluation skipped"
                self._progress("⚠ Evaluation skipped: no cases were evaluated")
            else:
                self._state.evaluation_status = "completed"
                self._state.evaluation_message = "Evaluation completed"
                self._progress("✓ Evaluation completed")
            self.print_final_summary(run_manifest_path)
            return

        if event_type == "run_failed":
            error = payload.get("error")
            if isinstance(error, str) and error:
                print(f"{STATUS_PREFIX} Run failed: {error}", file=sys.stderr)
            return

    def print_final_summary(self, run_manifest_path: Path) -> None:
        """Render final textual report summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        print()
        if summary.security_status == "unavailable" and summary.scored_cases == 0:
            print("Evaluation")
            print("⚠ Skipped  no cases were evaluated")
            print()
        print("Cases")
        _print_plain_rows(_cases_rows(summary))
        print()
        print("Case Execution")
        _print_plain_rows(_execution_rows(summary))
        print()
        print("Trap Evaluation")
        _print_plain_rows(_evaluation_rows(summary))
        if self.verbose:
            print()
            print("Artifacts")
            _print_plain_rows(_artifact_rows(run_manifest_path))

    def _on_run_started(self, payload: Mapping[str, object]) -> None:
        trap_id = payload.get("trap_id")
        if isinstance(trap_id, str) and trap_id:
            self._state.trap_id = trap_id
        target = payload.get("target")
        if isinstance(target, str) and target:
            self._state.target = target
        harness_command = payload.get("harness_command")
        if isinstance(harness_command, str) and harness_command:
            self._state.harness_command = harness_command
        run_dir = payload.get("run_dir")
        if isinstance(run_dir, str) and run_dir:
            self._state.run_dir = _display_path(Path(run_dir))
        run_manifest_path = payload.get("run_manifest_path")
        if isinstance(run_manifest_path, str) and run_manifest_path:
            self._state.run_manifest_path = run_manifest_path
        counts_payload = payload.get("counts")
        if isinstance(counts_payload, Mapping):
            counts = _require_counts_payload(payload)
            self._state.generated_artifacts = counts["generated_artifacts"]
            self._state.scenario_cases = counts["scenario_cases"]
            self._state.base_cases = counts["base_cases"]
            self._state.variant_cases = counts["variant_cases"]
            self._state.selected_cases = counts["selected_cases"]
            self._state.harness_executed = counts["harness_executed"]
            self._state.harness_passed = counts["harness_passed"]
            self._state.harness_failed = counts["harness_failed"]
            self._state.scored_cases = counts["scored_cases"]
            self._state.trap_successes = counts["trap_successes"]
        if not self._run_header_printed:
            print("OpenTrap Run")
            print(f"Trap:      {self._state.trap_id}")
            print(f"Target:    {self._state.target}")
            if self._state.run_dir != "-":
                print(f"Run:       {self._state.run_dir}")
            print()
            self._run_header_printed = True

    def _progress(self, message: str) -> None:
        print(message)

    def _verbose_status(self, message: str) -> None:
        if self.verbose:
            print(f"{STATUS_PREFIX} {message}", file=sys.stderr)

    def _print_harness_output(self, payload: Mapping[str, object]) -> None:
        index = _int_or_default(payload.get("display_case_index"), default=0)
        total = _int_or_default(payload.get("selected_cases"), default=0)
        exit_code = _int_or_default(payload.get("exit_code"), default=1)
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        stdout_text = stdout if isinstance(stdout, str) else ""
        stderr_text = stderr if isinstance(stderr, str) else ""
        if not stdout_text.strip() and not stderr_text.strip():
            return

        print(f"Harness output case {index}/{total} (exit {exit_code})", file=sys.stderr)
        if stdout_text.strip():
            print("stdout:", file=sys.stderr)
            print(stdout_text.rstrip(), file=sys.stderr)
        if stderr_text.strip():
            print("stderr:", file=sys.stderr)
            print(stderr_text.rstrip(), file=sys.stderr)

    def _print_evaluation_phase(self, payload: Mapping[str, object]) -> None:
        phase = payload.get("phase")
        detail = payload.get("detail")
        if isinstance(phase, str) and phase:
            token = f"evaluation.{phase}"
            rendered = f"{token}: {detail}" if isinstance(detail, str) and detail else token
            print(rendered, file=sys.stderr)

    def _print_evaluation_progress(self, payload: Mapping[str, object]) -> None:
        processed = _int_or_default(payload.get("processed"), default=0)
        total = _int_or_default(payload.get("total"), default=0)
        if total > 0:
            percent = (processed / total) * 100.0
            print(
                f"evaluation.progress: {processed}/{total} ({percent:.1f}%)",
                file=sys.stderr,
            )

    def _print_evaluation_output(self, payload: Mapping[str, object]) -> None:
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        stdout_text = stdout if isinstance(stdout, str) else ""
        stderr_text = stderr if isinstance(stderr, str) else ""
        if stdout_text.strip():
            print("Evaluation stdout:", file=sys.stderr)
            print(stdout_text.rstrip(), file=sys.stderr)
        if stderr_text.strip():
            print("Evaluation stderr:", file=sys.stderr)
            print(stderr_text.rstrip(), file=sys.stderr)


class RichRenderer:
    """Rich renderer for interactive terminals."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self.console = Console(stderr=True)
        self.stdout = Console()
        self._state = _RunDisplayState()
        self._live: Live | None = None
        self._verbose_lines: list[str] = []

    def __call__(self, event: RunEvent) -> None:
        event_type = event.type
        payload = event.payload

        if event_type == "run_started":
            self._on_run_started(payload)
            return

        if event_type == "generate_started":
            self._state.generation_status = "running"
            self._state.generation_message = "Generating dataset"
            self._refresh()
            return

        if event_type == "generate_progress":
            state = payload.get("state")
            elapsed = payload.get("elapsed_seconds")
            if state == "cache_hit":
                self._state.dataset_from_cache = True
                self._state.generation_message = "Dataset cache hit"
                self._verbose_status("Dataset cache hit")
            elif state == "cache_miss":
                self._state.dataset_from_cache = False
                self._verbose_status("Dataset cache miss")
            elif state == "generating" and isinstance(elapsed, int):
                self._state.generation_message = f"Generating dataset ({elapsed}s)"
                self._verbose_status(f"Generating dataset... ({elapsed}s)")
            elif state == "adapter_wait" and isinstance(elapsed, int):
                self._state.adapter_message = f"Waiting for adapter health ({elapsed}s)"
                self._verbose_status(f"Waiting for adapter health... ({elapsed}s)")
            self._refresh()
            return

        if event_type == "generate_completed":
            counts = _require_counts_payload(payload)
            self._state.generated_artifacts = counts["generated_artifacts"]
            self._state.scenario_cases = counts["scenario_cases"]
            self._state.base_cases = counts["base_cases"]
            self._state.variant_cases = counts["variant_cases"]
            self._state.selected_cases = counts["selected_cases"]
            source = "(from cache)" if self._state.dataset_from_cache else ""
            self._state.generation_status = "completed"
            self._state.generation_message = f"Dataset generated {source}".strip()
            self._refresh()
            return

        if event_type == "adapter_launching":
            self._state.adapter_status = "running"
            endpoint = _adapter_endpoint(payload)
            suffix = f" ({endpoint})" if endpoint else ""
            self._state.adapter_message = f"Launching adapter{suffix}"
            self._refresh()
            return

        if event_type == "adapter_ready":
            self._state.adapter_status = "completed"
            endpoint = _adapter_endpoint(payload)
            suffix = f" ({endpoint})" if endpoint else ""
            self._state.adapter_message = f"Adapter ready{suffix}"
            self._refresh()
            return

        if event_type == "adapter_status_update":
            message = payload.get("message")
            if isinstance(message, str) and message:
                self._verbose_status(f"Adapter: {message}")
            return

        if event_type == "adapter_log":
            message = payload.get("message")
            if isinstance(message, str) and self.verbose:
                self._append_verbose(f"Adapter log: {message}")
            return

        if event_type == "case_started":
            self._state.harness_status = "running"
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("selected_cases"), default=0)
            self._state.harness_message = f"Progress: {max(0, index - 1)} / {total}"
            self._refresh()
            return

        if event_type == "harness_output":
            if self.verbose:
                self._print_harness_output(payload)
            return

        if event_type == "case_finished":
            self._state.harness_executed = _int_or_default(
                payload.get("harness_executed"),
                default=0,
            )
            self._state.harness_passed = _int_or_default(payload.get("harness_passed"), default=0)
            self._state.harness_failed = _int_or_default(payload.get("harness_failed"), default=0)
            selected = _int_or_default(
                payload.get("selected_cases"),
                default=self._state.selected_cases,
            )
            self._state.selected_cases = selected
            self._state.harness_message = (
                f"Progress: {self._state.harness_executed} / {self._state.selected_cases}"
            )
            self._refresh()
            return

        if event_type == "run_finalized":
            counts = _require_counts_payload(payload)
            self._state.harness_executed = counts["harness_executed"]
            self._state.harness_passed = counts["harness_passed"]
            self._state.harness_failed = counts["harness_failed"]
            if self._state.harness_failed:
                self._state.harness_status = "failed"
                self._state.harness_message = "Harness completed with failures"
            else:
                self._state.harness_status = "completed"
                self._state.harness_message = "Harness completed"
            self._refresh()
            return

        if event_type == "evaluate_started":
            self._state.evaluation_status = "running"
            self._state.evaluation_message = "Evaluating results"
            self._refresh()
            return

        if event_type == "evaluate_phase":
            phase = payload.get("phase")
            if isinstance(phase, str) and phase:
                self._state.evaluation_message = f"Evaluation: {phase}"
                if self.verbose:
                    self._print_evaluation_phase(payload)
            self._refresh()
            return

        if event_type == "evaluate_progress":
            processed = _int_or_default(payload.get("processed"), default=0)
            total = _int_or_default(payload.get("total"), default=0)
            if total > 0:
                self._state.evaluation_message = f"Evaluation {processed}/{total}"
                if self.verbose:
                    self._print_evaluation_progress(payload)
            self._refresh()
            return

        if event_type == "evaluation_output":
            if self.verbose:
                self._append_evaluation_output(payload)
            self._refresh()
            return

        if event_type == "evaluate_completed":
            run_manifest_path = _path_from_payload(payload, "run_manifest_path")
            if run_manifest_path is None:
                return
            summary = load_security_summary(run_manifest_path)
            self._state.scored_cases = summary.scored_cases
            self._state.trap_successes = summary.trap_successes
            self._state.trap_outcome = summary.display_status
            self._state.trap_success_rate = summary.rate_percent
            self._state.report_path = summary.report_path
            if summary.security_status == "unavailable" and summary.scored_cases == 0:
                self._state.evaluation_status = "skipped"
                self._state.evaluation_message = "Evaluation skipped"
            else:
                self._state.evaluation_status = "completed"
                self._state.evaluation_message = "Evaluation completed"
            self._refresh()
            self._stop_live()
            return

        if event_type == "run_failed":
            stage = payload.get("stage")
            if stage == "evaluate":
                self._state.evaluation_status = "failed"
            elif stage == "run":
                if self._state.adapter_status == "running":
                    self._state.adapter_status = "failed"
                elif self._state.harness_status == "running":
                    self._state.harness_status = "failed"
            self._refresh()
            self._stop_live()
            error = payload.get("error")
            if isinstance(error, str) and error:
                self.console.print(f"[red]Run failed:[/red] {escape(error)}")
            return

    def print_final_summary(self, run_manifest_path: Path) -> None:
        """Render final rich panel summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        self.stdout.print(
            Panel(_build_rich_rows(_cases_rows(summary)), title="Cases", border_style="yellow")
        )
        self.stdout.print(
            Panel(
                _build_rich_rows(_execution_rows(summary)),
                title="Case Execution",
                border_style="cyan",
            )
        )
        eval_rows = _evaluation_rows(summary)
        self.stdout.print(
            Panel(
                _build_rich_rows(eval_rows),
                title="Trap Evaluation",
                border_style="magenta",
            )
        )
        if self.verbose:
            self.stdout.print(
                Panel(
                    _build_rich_rows(_artifact_rows(run_manifest_path)),
                    title="Artifacts",
                    border_style="blue",
                )
            )

    def _on_run_started(self, payload: Mapping[str, object]) -> None:
        trap_id = payload.get("trap_id")
        if isinstance(trap_id, str) and trap_id:
            self._state.trap_id = trap_id
        target = payload.get("target")
        if isinstance(target, str) and target:
            self._state.target = target
        harness_command = payload.get("harness_command")
        if isinstance(harness_command, str) and harness_command:
            self._state.harness_command = harness_command
        run_dir = payload.get("run_dir")
        if isinstance(run_dir, str) and run_dir:
            self._state.run_dir = _display_path(Path(run_dir))
        run_manifest_path = payload.get("run_manifest_path")
        if isinstance(run_manifest_path, str) and run_manifest_path:
            self._state.run_manifest_path = run_manifest_path
        counts_payload = payload.get("counts")
        if isinstance(counts_payload, Mapping):
            counts = _require_counts_payload(payload)
            self._state.generated_artifacts = counts["generated_artifacts"]
            self._state.scenario_cases = counts["scenario_cases"]
            self._state.base_cases = counts["base_cases"]
            self._state.variant_cases = counts["variant_cases"]
            self._state.selected_cases = counts["selected_cases"]
            self._state.harness_executed = counts["harness_executed"]
            self._state.harness_passed = counts["harness_passed"]
            self._state.harness_failed = counts["harness_failed"]
            self._state.scored_cases = counts["scored_cases"]
            self._state.trap_successes = counts["trap_successes"]
        if self._live is None:
            self._start_live()
        else:
            self._refresh()

    def _start_live(self) -> None:
        self._stop_live()
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self._live.start()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Group:
        header = Table.grid()
        header.add_column()
        header.add_row("[bold cyan]OpenTrap[/bold cyan] run")

        config_rows = [
            ("Trap", escape(self._state.trap_id)),
            ("Target", escape(self._state.target)),
            ("Run", escape(self._state.run_dir)),
        ]
        config = _build_rich_rows(config_rows)

        steps = Table.grid(padding=(0, 2))
        steps.add_column(width=3)
        steps.add_column()
        steps.add_row(
            *self._step_cells(self._state.generation_status, self._state.generation_message)
        )
        steps.add_row(*self._step_cells(self._state.adapter_status, self._state.adapter_message))
        steps.add_row(*self._step_cells(self._state.harness_status, self._state.harness_message))
        steps.add_row(
            *self._step_cells(self._state.evaluation_status, self._state.evaluation_message)
        )

        counts_panel = Panel(
            _build_rich_rows(
                [
                    ("Scenario cases", str(self._state.scenario_cases)),
                    ("  Base", str(self._state.base_cases)),
                    ("  Variants", str(self._state.variant_cases)),
                    ("Selected", str(self._state.selected_cases)),
                ]
            ),
            title="Cases",
            border_style="yellow",
        )
        execution_panel = Panel(
            _build_rich_rows(
                [
                    ("Harness", escape(self._state.harness_command)),
                    (
                        "Progress",
                        f"{self._state.harness_executed} / {self._state.selected_cases}",
                    ),
                    ("Harness Passed", str(self._state.harness_passed)),
                    ("Harness Failed", str(self._state.harness_failed)),
                ]
            ),
            title="Case Execution",
            border_style="cyan",
        )
        evaluation_panel = Panel(
            _build_rich_rows(
                [
                    ("Scored cases", str(self._state.scored_cases)),
                    (
                        "Trap successes",
                        f"{self._state.trap_successes} / {self._state.scored_cases}",
                    ),
                    ("Success rate", self._state.trap_success_rate),
                    ("Outcome", escape(self._state.trap_outcome)),
                    ("Report", escape(self._state.report_path)),
                ]
            ),
            title="Trap Evaluation",
            border_style="magenta",
        )

        renderables: list[object] = [
            header,
            Panel(config, title="Run Configuration", border_style="blue"),
            Panel(steps, title="Progress", border_style="green"),
            counts_panel,
            execution_panel,
            evaluation_panel,
        ]
        if self.verbose:
            renderables.append(self._render_verbose_output())
        return Group(*renderables)

    def _step_cells(self, status: str, message: str) -> tuple[object, str]:
        escaped = escape(message)
        if status == "running":
            return Spinner("dots", style="cyan"), f"[cyan]{escaped}[/cyan]"
        if status == "completed":
            return "[green]✓[/green]", f"[green]{escaped}[/green]"
        if status == "failed":
            return "[red]✗[/red]", f"[red]{escaped}[/red]"
        if status == "skipped":
            return "[blue]🛈[/blue]", f"[blue]{escaped}[/blue]"
        return "[dim]-[/dim]", f"[dim]{escaped}[/dim]"

    def _verbose_status(self, message: str) -> None:
        if self.verbose:
            self._append_verbose(f"{STATUS_PREFIX} {message}")
            self._refresh()

    def _print_harness_output(self, payload: Mapping[str, object]) -> None:
        index = _int_or_default(payload.get("display_case_index"), default=0)
        total = _int_or_default(payload.get("selected_cases"), default=0)
        exit_code = _int_or_default(payload.get("exit_code"), default=1)
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        stdout_text = stdout if isinstance(stdout, str) else ""
        stderr_text = stderr if isinstance(stderr, str) else ""
        if not stdout_text.strip() and not stderr_text.strip():
            return

        self._append_verbose(f"Harness case {index}/{total} output (exit {exit_code})")
        if stdout_text.strip():
            self._append_verbose("Harness stdout:")
            self._append_verbose_block(stdout_text)
        if stderr_text.strip():
            self._append_verbose("Harness stderr:")
            self._append_verbose_block(stderr_text)
        self._refresh()

    def _print_evaluation_phase(self, payload: Mapping[str, object]) -> None:
        phase = payload.get("phase")
        detail = payload.get("detail")
        if isinstance(phase, str) and phase:
            token = f"evaluation.{phase}"
            rendered = f"{token}: {detail}" if isinstance(detail, str) and detail else token
            self._append_verbose(f"Evaluation: {rendered}")

    def _print_evaluation_progress(self, payload: Mapping[str, object]) -> None:
        processed = _int_or_default(payload.get("processed"), default=0)
        total = _int_or_default(payload.get("total"), default=0)
        if total > 0:
            percent = (processed / total) * 100.0
            self._append_verbose(f"Evaluation: progress {processed}/{total} ({percent:.1f}%)")

    def _append_evaluation_output(self, payload: Mapping[str, object]) -> None:
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        stdout_text = stdout if isinstance(stdout, str) else ""
        stderr_text = stderr if isinstance(stderr, str) else ""
        if stdout_text.strip():
            self._append_verbose("Evaluation stdout:")
            self._append_verbose_block(stdout_text)
        if stderr_text.strip():
            self._append_verbose("Evaluation stderr:")
            self._append_verbose_block(stderr_text)

    def _append_verbose(self, line: str) -> None:
        self._verbose_lines.append(line)
        if len(self._verbose_lines) > VERBOSE_BUFFER_LIMIT:
            self._verbose_lines = self._verbose_lines[-VERBOSE_BUFFER_LIMIT:]

    def _append_verbose_block(self, text: str) -> None:
        lines = text.rstrip().splitlines()
        if not lines:
            return
        for line in lines:
            self._append_verbose(line)

    def _render_verbose_output(self) -> Panel:
        if self._verbose_lines:
            body = Text("\n".join(self._verbose_lines))
        else:
            body = Text("No verbose output yet", style="dim")
        return Panel(body, title="Verbose Output", border_style="magenta")


def build_renderer(*, verbose: bool = False) -> EventSink:
    """Choose rich or plain renderer based on current terminal capabilities."""
    if sys.stderr.isatty() and sys.stdout.isatty():
        return RichRenderer(verbose=verbose)
    return PlainRenderer(verbose=verbose)


def load_security_summary(run_manifest_path: Path) -> SecuritySummary:
    """Load normalized security summary fields from a run report."""
    report_path = run_manifest_path.parent / "report.json"
    report = load_json_maybe(report_path) or {}
    counts = report.get("counts")
    if not isinstance(counts, Mapping):
        raise RuntimeError("report.json is missing required counts payload")
    security_result = report.get("security_result")
    if not isinstance(security_result, Mapping):
        raise RuntimeError("report.json is missing required security_result payload")

    status_value = security_result.get("status")
    security_status = status_value if isinstance(status_value, str) else "unavailable"
    display_status = "vulnerable" if security_status == "vulnerable" else "secure"
    generated_artifacts = _required_int(counts, "generated_artifacts")
    scenario_cases = _required_int(counts, "scenario_cases")
    base_cases = _required_int(counts, "base_cases")
    variant_cases = _required_int(counts, "variant_cases")
    selected_cases = _required_int(counts, "selected_cases")
    harness_executed = _required_int(counts, "harness_executed")
    harness_passed = _required_int(counts, "harness_passed")
    harness_failed = _required_int(counts, "harness_failed")
    scored_cases = _required_int(counts, "scored_cases")
    trap_successes = _required_int(counts, "trap_successes")
    rate_percent = _format_percent(
        (trap_successes / scored_cases) if scored_cases > 0 else 0.0
    )

    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        run_id = run_manifest_path.parent.name

    return SecuritySummary(
        run_id=run_id,
        generated_artifacts=generated_artifacts,
        scenario_cases=scenario_cases,
        base_cases=base_cases,
        variant_cases=variant_cases,
        selected_cases=selected_cases,
        harness_executed=harness_executed,
        harness_passed=harness_passed,
        harness_failed=harness_failed,
        scored_cases=scored_cases,
        trap_successes=trap_successes,
        security_status=security_status,
        display_status=display_status,
        rate_percent=rate_percent,
        report_path=str(_preferred_report_artifact_path(run_manifest_path)),
    )


def _path_from_payload(payload: Mapping[str, object], key: str) -> Path | None:
    raw = payload.get(key)
    if isinstance(raw, str) and raw:
        return Path(raw)
    return None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _adapter_endpoint(payload: Mapping[str, object]) -> str | None:
    host = payload.get("host")
    port = payload.get("port")
    if isinstance(host, str) and host and isinstance(port, int):
        return f"{host}:{port}"
    if isinstance(port, int):
        return str(port)
    return None


def _cases_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    return [
        ("Scenario cases", str(summary.scenario_cases)),
        ("  Base", str(summary.base_cases)),
        ("  Variants", str(summary.variant_cases)),
        ("Selected", str(summary.selected_cases)),
    ]


def _execution_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    return [
        ("Progress", f"{summary.harness_executed} / {summary.selected_cases}"),
        ("Harness Passed", str(summary.harness_passed)),
        ("Harness Failed", str(summary.harness_failed)),
    ]


def _evaluation_rows(summary: SecuritySummary) -> list[tuple[str, str]]:
    rows = [("Scored cases", str(summary.scored_cases))]
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


def _preferred_report_artifact_path(run_manifest_path: Path) -> Path:
    html_report_path = run_manifest_path.parent / "evaluation_report.html"
    if html_report_path.exists():
        return html_report_path
    return run_manifest_path.parent / "evaluation.csv"


def _artifact_rows(run_manifest_path: Path) -> list[tuple[str, str]]:
    return [
        ("Run manifest", _display_path(run_manifest_path)),
        ("Sessions", _display_path(run_manifest_path.parent / "sessions.jsonl")),
        ("Traces", _display_path(run_manifest_path.parent / "traces.jsonl")),
    ]


def _build_rich_rows(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="bold cyan")
    table.add_column()
    for label, value in rows:
        table.add_row(label, value)
    return table


def _print_plain_rows(rows: list[tuple[str, str]]) -> None:
    label_width = max((len(label) for label, _value in rows), default=0)
    for label, value in rows:
        print(f"{label:<{label_width}}  {value}")


def _int_or_default(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _format_percent(value: object) -> str:
    if isinstance(value, int | float):
        return f"{float(value) * 100.0:.1f}%"
    return "0.0%"


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    raise RuntimeError(f"missing required integer field: {key}")


def _require_counts_payload(payload: Mapping[str, object]) -> dict[str, int]:
    raw_counts = payload.get("counts")
    if not isinstance(raw_counts, Mapping):
        raise RuntimeError("event payload is missing required counts object")
    counts: dict[str, int] = {}
    for key in (
        "generated_artifacts",
        "scenario_cases",
        "base_cases",
        "variant_cases",
        "selected_cases",
        "harness_executed",
        "harness_passed",
        "harness_failed",
        "scored_cases",
        "trap_successes",
    ):
        counts[key] = _required_int(raw_counts, key)
    return counts
