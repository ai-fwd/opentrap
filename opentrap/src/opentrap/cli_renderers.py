"""Renderers for OpenTrap CLI lifecycle events."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table

from opentrap.events import EventSink, RunEvent
from opentrap.io_utils import load_json_maybe

STATUS_PREFIX = "[opentrap]"


@dataclass(frozen=True)
class SecuritySummary:
    """Normalized report fields used by CLI final summary renderers."""

    run_id: str
    case_count: int
    failed_session_count: int
    security_status: str
    display_status: str
    trap_success_count: int
    evaluated_count: int
    rate_percent: str


class PlainRenderer:
    """Deterministic line renderer for non-TTY environments."""

    def __call__(self, event: RunEvent) -> None:
        event_type = event.type
        payload = event.payload

        if event_type == "run_started":
            trap_id = payload.get("trap_id")
            self._status(f"Run started for trap '{trap_id}'")
            return

        if event_type == "generate_started":
            self._status("Generating dataset")
            return

        if event_type == "generate_progress":
            state = payload.get("state")
            elapsed = payload.get("elapsed_seconds")
            if state == "cache_hit":
                self._status("Dataset cache hit")
            elif state == "cache_miss":
                self._status("Dataset cache miss")
            elif state == "generating" and isinstance(elapsed, int):
                self._status(f"Generating dataset... ({elapsed}s)")
            elif state == "adapter_wait" and isinstance(elapsed, int):
                self._status(f"Waiting for adapter health... ({elapsed}s)")
            return

        if event_type == "generate_completed":
            trap_id = str(payload.get("trap_id") or "-")
            case_count = _int_or_default(payload.get("case_count"), default=0)
            print("Generation summary")
            print("Trap | Cases")
            print(f"{trap_id} | {case_count}")
            return

        if event_type == "adapter_launching":
            product = payload.get("product_under_test")
            self._status(f"Launching adapter runtime for product '{product}'")
            return

        if event_type == "adapter_ready":
            self._status("Adapter ready")
            return

        if event_type == "adapter_status_update":
            message = payload.get("message")
            if isinstance(message, str):
                self._status(f"Adapter: {message}")
            return

        if event_type == "case_started":
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("total_cases"), default=0)
            self._status(f"Starting case {index}/{total}")
            return

        if event_type == "case_finished":
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("total_cases"), default=0)
            succeeded = bool(payload.get("succeeded"))
            if succeeded:
                self._status(f"Case {index}/{total} completed")
            else:
                exit_code = _int_or_default(payload.get("exit_code"), default=1)
                self._status(f"Case {index}/{total} failed (exit code {exit_code})")
            return

        if event_type == "evaluate_started":
            self._status("Running trap evaluation")
            return

        if event_type == "evaluate_phase":
            phase = payload.get("phase")
            detail = payload.get("detail")
            if isinstance(phase, str) and phase:
                token = f"evaluation.{phase}"
                rendered = f"{token}: {detail}" if isinstance(detail, str) and detail else token
                print(rendered, file=sys.stderr)
            return

        if event_type == "evaluate_progress":
            processed = _int_or_default(payload.get("processed"), default=0)
            total = _int_or_default(payload.get("total"), default=0)
            if total > 0:
                percent = (processed / total) * 100.0
                print(
                    f"evaluation.progress: {processed}/{total} ({percent:.1f}%)",
                    file=sys.stderr,
                )
            return

        if event_type == "evaluate_completed":
            run_manifest_path_raw = payload.get("run_manifest_path")
            if isinstance(run_manifest_path_raw, str) and run_manifest_path_raw:
                self.print_final_summary(Path(run_manifest_path_raw))
            return

        if event_type == "run_finalized":
            self._status("Run finalized")
            return

        if event_type == "run_failed":
            error = payload.get("error")
            if isinstance(error, str) and error:
                self._status(f"Run failed: {error}")
            return

    def print_final_summary(self, run_manifest_path: Path) -> None:
        """Render final textual report summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        print("OpenTrap run complete")
        print()
        print(f"Trap cases executed: {summary.case_count}")
        print(f"Harness failures: {summary.failed_session_count}")
        print()
        print(f"Security result: {summary.display_status}")
        if summary.security_status == "unavailable" and summary.evaluated_count == 0:
            print("Reason: no cases were evaluated")
        else:
            print(f"Trap successes: {summary.trap_success_count} / {summary.evaluated_count}")
            print(f"Success rate: {summary.rate_percent}")
        print()
        print("Detailed report:")
        print(f"runs/{summary.run_id}/evaluation.csv")

    def _status(self, message: str) -> None:
        print(f"{STATUS_PREFIX} {message}", file=sys.stderr)


class RichRenderer:
    """Rich renderer for interactive terminals."""

    def __init__(self) -> None:
        self.console = Console(stderr=True)
        self.stdout = Console()
        self._generate_status: Status | None = None
        self._evaluate_status: Status | None = None
        self._run_status: Status | None = None

    def __call__(self, event: RunEvent) -> None:
        event_type = event.type
        payload = event.payload

        if event_type == "run_started":
            trap_id = payload.get("trap_id")
            self.console.print(f"[bold cyan]Run started[/bold cyan] for [bold]{trap_id}[/bold]")
            return

        if event_type == "generate_started":
            self._start_generate_status("Generating dataset...")
            return

        if event_type == "generate_progress":
            if self._generate_status is None:
                self._start_generate_status("Generating dataset...")
            state = payload.get("state")
            elapsed = payload.get("elapsed_seconds")
            if state == "cache_hit":
                self._update_generate_status("Dataset cache hit")
            elif state == "cache_miss":
                self._update_generate_status("Dataset cache miss")
            elif state == "generating" and isinstance(elapsed, int):
                self._update_generate_status(f"Generating dataset... ({elapsed}s)")
            elif state == "adapter_wait" and isinstance(elapsed, int):
                self._update_generate_status(f"Waiting for adapter health... ({elapsed}s)")
            return

        if event_type == "generate_completed":
            self._stop_generate_status()
            table = Table(title="Generation Summary")
            table.add_column("Trap")
            table.add_column("Cases", justify="right")
            table.add_row(
                str(payload.get("trap_id") or "-"),
                str(_int_or_default(payload.get("case_count"), default=0)),
            )
            self.stdout.print(table)
            return

        if event_type == "adapter_launching":
            self.console.print("[cyan]Launching adapter runtime...[/cyan]")
            return

        if event_type == "adapter_ready":
            self.console.print("[green]Adapter ready[/green]")
            return

        if event_type == "adapter_status_update":
            message = payload.get("message")
            if isinstance(message, str):
                self.console.print(f"[magenta]Adapter[/magenta]: {message}")
            return

        if event_type == "case_started":
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("total_cases"), default=0)
            if self._run_status is None:
                self._run_status = self.console.status("Running cases...", spinner="dots")
                self._run_status.start()
            self._run_status.update(status=f"Running case {index}/{total}")
            return

        if event_type == "case_finished":
            index = _int_or_default(payload.get("display_case_index"), default=0)
            total = _int_or_default(payload.get("total_cases"), default=0)
            succeeded = bool(payload.get("succeeded"))
            result = "ok" if succeeded else "failed"
            if self._run_status is not None:
                self._run_status.update(status=f"Case {index}/{total} {result}")
            return

        if event_type == "evaluate_started":
            if self._evaluate_status is None:
                self._evaluate_status = self.console.status("Evaluating...", spinner="dots")
                self._evaluate_status.start()
            return

        if event_type == "evaluate_phase":
            phase = payload.get("phase")
            if isinstance(phase, str) and phase and self._evaluate_status is not None:
                self._evaluate_status.update(status=f"Evaluating: {phase}")
            return

        if event_type == "evaluate_progress":
            processed = _int_or_default(payload.get("processed"), default=0)
            total = _int_or_default(payload.get("total"), default=0)
            if total > 0 and self._evaluate_status is not None:
                self._evaluate_status.update(status=f"Evaluating: {processed}/{total}")
            return

        if event_type == "evaluate_completed":
            self._stop_evaluate_status()
            run_manifest_path_raw = payload.get("run_manifest_path")
            if isinstance(run_manifest_path_raw, str) and run_manifest_path_raw:
                self.print_final_summary(Path(run_manifest_path_raw))
            return

        if event_type == "run_finalized":
            self._stop_run_status()
            return

        if event_type == "run_failed":
            self._stop_generate_status()
            self._stop_run_status()
            self._stop_evaluate_status()
            error = payload.get("error")
            if isinstance(error, str) and error:
                self.console.print(f"[red]Run failed:[/red] {error}")
            return

    def print_final_summary(self, run_manifest_path: Path) -> None:
        """Render final rich panel summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        body = (
            f"[bold]Trap cases executed:[/bold] {summary.case_count}\n"
            f"[bold]Harness failures:[/bold] {summary.failed_session_count}\n"
            f"[bold]Security result:[/bold] {summary.display_status}\n"
            "[bold]Trap successes:[/bold] "
            f"{summary.trap_success_count} / {summary.evaluated_count}\n"
            f"[bold]Success rate:[/bold] {summary.rate_percent}\n"
            f"[bold]Detailed report:[/bold] runs/{summary.run_id}/evaluation.csv"
        )
        if summary.security_status == "unavailable" and summary.evaluated_count == 0:
            body = (
                f"[bold]Trap cases executed:[/bold] {summary.case_count}\n"
                f"[bold]Harness failures:[/bold] {summary.failed_session_count}\n"
                f"[bold]Security result:[/bold] {summary.display_status}\n"
                "[bold]Reason:[/bold] no cases were evaluated\n"
                f"[bold]Detailed report:[/bold] runs/{summary.run_id}/evaluation.csv"
            )
        self.stdout.print(Panel(body, title="OpenTrap Run Complete"))

    def _start_generate_status(self, status: str) -> None:
        self._stop_generate_status()
        self._generate_status = self.console.status(status, spinner="dots")
        self._generate_status.start()

    def _update_generate_status(self, status: str) -> None:
        if self._generate_status is not None:
            self._generate_status.update(status=status)

    def _stop_generate_status(self) -> None:
        if self._generate_status is not None:
            self._generate_status.stop()
            self._generate_status = None

    def _stop_run_status(self) -> None:
        if self._run_status is not None:
            self._run_status.stop()
            self._run_status = None

    def _stop_evaluate_status(self) -> None:
        if self._evaluate_status is not None:
            self._evaluate_status.stop()
            self._evaluate_status = None


def build_renderer() -> EventSink:
    """Choose rich or plain renderer based on current terminal capabilities."""
    if sys.stderr.isatty() and sys.stdout.isatty():
        return RichRenderer()
    return PlainRenderer()


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
        failed_session_count=failed_session_count,
        security_status=security_status,
        display_status=display_status,
        trap_success_count=trap_success_count,
        evaluated_count=evaluated_count,
        rate_percent=rate_percent,
    )


def _int_or_default(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _format_percent(value: object) -> str:
    if isinstance(value, int | float):
        return f"{float(value) * 100.0:.1f}%"
    return "0.0%"
