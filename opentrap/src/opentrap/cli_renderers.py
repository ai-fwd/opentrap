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
    case_count: int
    session_count: int
    failed_session_count: int
    security_status: str
    display_status: str
    trap_success_count: int
    evaluated_count: int
    rate_percent: str

    @property
    def passed_session_count(self) -> int:
        return max(0, self.session_count - self.failed_session_count)


@dataclass
class _RunDisplayState:
    trap_id: str = "-"
    run_dir: str = "-"
    run_manifest_path: str = ""
    mode: str = "run"
    case_count: int | None = None
    executing_case_count: int | None = None
    dataset_from_cache: bool = False
    generation_status: str = "pending"
    generation_message: str = "Dataset pending"
    adapter_status: str = "pending"
    adapter_message: str = "Adapter pending"
    harness_status: str = "pending"
    harness_message: str = "Harness pending"
    harness_passed: int = 0
    harness_failed: int = 0
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
            case_count = _int_or_default(payload.get("case_count"), default=0)
            executing = _int_or_default(payload.get("executing_case_count"), default=case_count)
            self._state.case_count = case_count
            self._state.executing_case_count = executing
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
            total = _int_or_default(payload.get("total_cases"), default=0)
            self._state.harness_message = f"Running case {index}/{total}"
            return

        if event_type == "harness_output":
            if self.verbose:
                self._print_harness_output(payload)
            return

        if event_type == "case_finished":
            succeeded = bool(payload.get("succeeded"))
            if succeeded:
                self._state.harness_passed += 1
            else:
                self._state.harness_failed += 1
            total = _int_or_default(payload.get("total_cases"), default=0)
            completed = self._state.harness_passed + self._state.harness_failed
            self._state.harness_message = f"Harness cases {completed}/{total}"
            return

        if event_type == "run_finalized":
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
            if summary.security_status == "unavailable" and summary.evaluated_count == 0:
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
        if summary.security_status == "unavailable" and summary.evaluated_count == 0:
            print("Evaluation")
            print("⚠ Skipped  no cases were evaluated")
            print()
        print("Summary")
        _print_plain_rows(_summary_rows(summary, run_manifest_path))
        if self.verbose:
            print()
            print("Artifacts")
            _print_plain_rows(_artifact_rows(run_manifest_path))

    def _on_run_started(self, payload: Mapping[str, object]) -> None:
        trap_id = payload.get("trap_id")
        if isinstance(trap_id, str) and trap_id:
            self._state.trap_id = trap_id
        run_dir = payload.get("run_dir")
        if isinstance(run_dir, str) and run_dir:
            self._state.run_dir = _display_path(Path(run_dir))
        run_manifest_path = payload.get("run_manifest_path")
        if isinstance(run_manifest_path, str) and run_manifest_path:
            self._state.run_manifest_path = run_manifest_path
        mode = payload.get("mode")
        if isinstance(mode, str) and mode:
            self._state.mode = mode
        if self._state.mode == "fast_eval":
            self._mark_execution_stages_skipped()
        case_count = payload.get("case_count")
        if isinstance(case_count, int):
            self._state.case_count = case_count
        if not self._run_header_printed:
            print("OpenTrap Run")
            print(f"Trap:      {self._state.trap_id}")
            if self._state.case_count is not None:
                print(f"Cases:     {self._state.case_count}")
            if self._state.run_dir != "-":
                print(f"Run:       {self._state.run_dir}")
            print()
            self._run_header_printed = True

    def _mark_execution_stages_skipped(self) -> None:
        self._state.generation_status = "skipped"
        self._state.generation_message = "Dataset skipped"
        self._state.adapter_status = "skipped"
        self._state.adapter_message = "Adapter skipped"
        self._state.harness_status = "skipped"
        self._state.harness_message = "Harness skipped"

    def _progress(self, message: str) -> None:
        print(message)

    def _verbose_status(self, message: str) -> None:
        if self.verbose:
            print(f"{STATUS_PREFIX} {message}", file=sys.stderr)

    def _print_harness_output(self, payload: Mapping[str, object]) -> None:
        index = _int_or_default(payload.get("display_case_index"), default=0)
        total = _int_or_default(payload.get("total_cases"), default=0)
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
            case_count = _int_or_default(payload.get("case_count"), default=0)
            executing = _int_or_default(payload.get("executing_case_count"), default=case_count)
            self._state.case_count = case_count
            self._state.executing_case_count = executing
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
            total = _int_or_default(payload.get("total_cases"), default=0)
            self._state.harness_message = f"Running case {index}/{total}"
            self._refresh()
            return

        if event_type == "harness_output":
            if self.verbose:
                self._print_harness_output(payload)
            return

        if event_type == "case_finished":
            if bool(payload.get("succeeded")):
                self._state.harness_passed += 1
            else:
                self._state.harness_failed += 1
            total = _int_or_default(payload.get("total_cases"), default=0)
            completed = self._state.harness_passed + self._state.harness_failed
            self._state.harness_message = f"Harness cases {completed}/{total}"
            self._refresh()
            return

        if event_type == "run_finalized":
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
            if summary.security_status == "unavailable" and summary.evaluated_count == 0:
                self._state.evaluation_status = "skipped"
                self._state.evaluation_message = "Evaluation skipped"
            else:
                self._state.evaluation_status = "completed"
                self._state.evaluation_message = "Evaluation completed"
            self._refresh()
            self._stop_live()
            self.print_final_summary(run_manifest_path)
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
        table = _build_rich_rows(_summary_rows(summary, run_manifest_path))
        if self.verbose:
            for label, value in _artifact_rows(run_manifest_path):
                table.add_row(label, value)
        self.stdout.print(Panel(table, title="Summary", border_style="blue"))

    def _on_run_started(self, payload: Mapping[str, object]) -> None:
        trap_id = payload.get("trap_id")
        if isinstance(trap_id, str) and trap_id:
            self._state.trap_id = trap_id
        run_dir = payload.get("run_dir")
        if isinstance(run_dir, str) and run_dir:
            self._state.run_dir = _display_path(Path(run_dir))
        run_manifest_path = payload.get("run_manifest_path")
        if isinstance(run_manifest_path, str) and run_manifest_path:
            self._state.run_manifest_path = run_manifest_path
        mode = payload.get("mode")
        if isinstance(mode, str) and mode:
            self._state.mode = mode
        if self._state.mode == "fast_eval":
            self._mark_execution_stages_skipped()
        case_count = payload.get("case_count")
        if isinstance(case_count, int):
            self._state.case_count = case_count
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

        config_rows = [("Trap", escape(self._state.trap_id))]
        if self._state.case_count is not None:
            config_rows.append(("Cases", str(self._state.case_count)))
        config_rows.append(("Run", escape(self._state.run_dir)))
        config = _build_rich_rows(config_rows)

        steps = Table.grid(padding=(0, 2))
        steps.add_column(width=3)
        steps.add_column()
        steps.add_row(
            *self._step_cells(self._state.generation_status, self._state.generation_message)
        )
        steps.add_row(*self._step_cells(self._state.adapter_status, self._state.adapter_message))
        harness_detail = self._state.harness_message
        if self._state.harness_status in {"running", "completed", "failed"}:
            harness_detail = (
                f"{self._state.harness_message} "
                f"({self._state.harness_passed} passed, {self._state.harness_failed} failed)"
            )
        steps.add_row(*self._step_cells(self._state.harness_status, harness_detail))
        steps.add_row(
            *self._step_cells(self._state.evaluation_status, self._state.evaluation_message)
        )

        renderables: list[object] = [
            header,
            Panel(config, title="Run Configuration", border_style="blue"),
            Panel(steps, title="Progress", border_style="green"),
        ]
        if self.verbose:
            renderables.append(self._render_verbose_output())
        return Group(*renderables)

    def _mark_execution_stages_skipped(self) -> None:
        self._state.generation_status = "skipped"
        self._state.generation_message = "Dataset skipped"
        self._state.adapter_status = "skipped"
        self._state.adapter_message = "Adapter skipped"
        self._state.harness_status = "skipped"
        self._state.harness_message = "Harness skipped"

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
        total = _int_or_default(payload.get("total_cases"), default=0)
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
    security_result = report.get("security_result")
    if not isinstance(security_result, Mapping):
        security_result = {
            "status": "unavailable",
            "trap_success_count": 0,
            "trap_failure_count": 0,
            "evaluated_count": 0,
            "trap_success_rate": None,
            "details": {},
        }

    status_value = security_result.get("status")
    security_status = status_value if isinstance(status_value, str) else "unavailable"
    display_status = (
        "no successful traps detected"
        if security_status == "no_successful_traps_detected"
        else security_status
    )

    case_count = _int_or_default(report.get("case_count"), default=0)
    session_count = _int_or_default(report.get("session_count"), default=case_count)
    failed_session_count = _int_or_default(report.get("failed_session_count"), default=0)
    trap_success_count = _int_or_default(security_result.get("trap_success_count"), default=0)
    evaluated_count = _int_or_default(security_result.get("evaluated_count"), default=0)
    trap_success_rate = security_result.get("trap_success_rate")
    rate_percent = _format_percent(trap_success_rate)

    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        run_id = run_manifest_path.parent.name

    return SecuritySummary(
        run_id=run_id,
        case_count=case_count,
        session_count=session_count,
        failed_session_count=failed_session_count,
        security_status=security_status,
        display_status=display_status,
        trap_success_count=trap_success_count,
        evaluated_count=evaluated_count,
        rate_percent=rate_percent,
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


def _summary_rows(summary: SecuritySummary, run_manifest_path: Path) -> list[tuple[str, str]]:
    rows = [("Trap result", summary.display_status)]
    if summary.security_status == "unavailable" and summary.evaluated_count == 0:
        rows.append(("Reason", "no cases were evaluated"))
    else:
        rows.extend(
            [
                ("Trap successes", f"{summary.trap_success_count} / {summary.evaluated_count}"),
                ("Success rate", summary.rate_percent),
            ]
        )
    rows.extend(
        [
            (
                "Harness",
                f"{summary.passed_session_count} passed, {summary.failed_session_count} failed",
            ),
            ("Report", _display_path(run_manifest_path.parent / "evaluation.csv")),
        ]
    )
    return rows


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
