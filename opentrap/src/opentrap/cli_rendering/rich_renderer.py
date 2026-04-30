"""Rich interactive CLI renderer backed by shared reducer + view model."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from opentrap.cli_rendering.display_state import RunDisplayState, load_security_summary
from opentrap.cli_rendering.event_reducer import reduce_event
from opentrap.cli_rendering.view_model import (
    artifact_rows,
    build_final_summary_view,
    build_run_view_model,
)
from opentrap.events import RunEvent

STATUS_PREFIX = "[opentrap]"
VERBOSE_BUFFER_LIMIT = 300


class RichRenderer:
    """Rich renderer for interactive terminals."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self.console = Console(stderr=True)
        self.stdout = Console()
        self._state = RunDisplayState()
        self._live: Live | None = None
        self._verbose_lines: list[str] = []

    def __call__(self, event: RunEvent) -> None:
        result = reduce_event(self._state, event)

        if result.run_started:
            if self._live is None:
                self._start_live()
            elif result.refresh:
                self._refresh()

        if result.status_message is not None and self.verbose and result.status_message:
            self._append_verbose(f"{STATUS_PREFIX} {result.status_message}")

        if result.adapter_log_message is not None and self.verbose:
            self._append_verbose(f"Adapter log: {result.adapter_log_message}")

        if result.harness_output_payload is not None and self.verbose:
            self._print_harness_output(result.harness_output_payload)

        if result.evaluation_phase_payload is not None and self.verbose:
            self._print_evaluation_phase(result.evaluation_phase_payload)

        if result.evaluation_progress_payload is not None and self.verbose:
            self._print_evaluation_progress(result.evaluation_progress_payload)

        if result.evaluation_output_payload is not None and self.verbose:
            self._append_evaluation_output(result.evaluation_output_payload)

        if result.refresh:
            self._refresh()

        if result.stop_live:
            self._stop_live()

        if result.run_failed_error is not None:
            self.console.print(f"[red]Run failed:[/red] {escape(result.run_failed_error)}")

    def print_final_summary(self, run_manifest_path: Path) -> None:
        """Render final rich panel summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        view = build_final_summary_view(summary)
        self.stdout.print(
            Panel(_build_rich_rows(view.cases_rows), title="Cases", border_style="yellow")
        )
        self.stdout.print(
            Panel(
                _build_rich_rows(view.execution_rows),
                title="Case Execution",
                border_style="cyan",
            )
        )
        self.stdout.print(
            Panel(
                _build_rich_rows(view.evaluation_rows),
                title="Trap Evaluation",
                border_style="magenta",
            )
        )
        if self.verbose:
            self.stdout.print(
                Panel(
                    _build_rich_rows(artifact_rows(run_manifest_path)),
                    title="Artifacts",
                    border_style="blue",
                )
            )

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
        model = build_run_view_model(self._state)

        header = Table.grid()
        header.add_column()
        header.add_row("[bold cyan]OpenTrap[/bold cyan] run")

        config = _build_rich_rows([(label, escape(value)) for label, value in model.config_rows])

        steps = Table.grid(padding=(0, 2))
        steps.add_column(width=3)
        steps.add_column()
        for step in model.steps:
            steps.add_row(*self._step_cells(step.status, step.message))

        counts_panel = Panel(
            _build_rich_rows(model.cases_rows),
            title="Cases",
            border_style="yellow",
        )
        execution_panel = Panel(
            _build_rich_rows([(label, escape(value)) for label, value in model.execution_rows]),
            title="Case Execution",
            border_style="cyan",
        )
        evaluation_panel = Panel(
            _build_rich_rows([(label, escape(value)) for label, value in model.evaluation_rows]),
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


def _build_rich_rows(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="bold cyan")
    table.add_column()
    for label, value in rows:
        table.add_row(label, value)
    return table


def _int_or_default(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default
