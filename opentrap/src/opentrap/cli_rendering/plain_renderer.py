"""Plain-text CLI renderer backed by shared reducer + view-model logic."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from opentrap.cli_rendering.display_state import RunDisplayState, load_security_summary
from opentrap.cli_rendering.event_reducer import reduce_event
from opentrap.cli_rendering.view_model import artifact_rows, build_final_summary_view
from opentrap.events import RunEvent

STATUS_PREFIX = "[opentrap]"


class PlainRenderer:
    """Deterministic line renderer for non-TTY environments."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._state = RunDisplayState()
        self._run_header_printed = False

    def __call__(self, event: RunEvent) -> None:
        result = reduce_event(self._state, event)

        if result.run_started and not self._run_header_printed:
            self._print_run_header()

        if result.progress_message is not None:
            self._progress(result.progress_message)

        if isinstance(result.status_message, str):
            self._verbose_status(result.status_message)

        if result.adapter_log_message is not None and self.verbose:
            print(f"Adapter log: {result.adapter_log_message}", file=sys.stderr)

        if result.harness_output_payload is not None and self.verbose:
            self._print_harness_output(result.harness_output_payload)

        if result.evaluation_phase_payload is not None and self.verbose:
            self._print_evaluation_phase(result.evaluation_phase_payload)

        if result.evaluation_progress_payload is not None and self.verbose:
            self._print_evaluation_progress(result.evaluation_progress_payload)

        if result.evaluation_output_payload is not None and self.verbose:
            self._print_evaluation_output(result.evaluation_output_payload)

        if result.final_summary_path is not None:
            self.print_final_summary(result.final_summary_path, stage=self._state.stage)

        if (
            event.type == "run_finalized"
            and self._state.stage == "execute"
            and isinstance(event.payload.get("run_manifest_path"), str)
        ):
            self.print_final_summary(
                Path(str(event.payload.get("run_manifest_path"))),
                stage="execute",
            )

        if result.run_failed_error is not None:
            print(f"{STATUS_PREFIX} Run failed: {result.run_failed_error}", file=sys.stderr)

    def print_final_summary(self, run_manifest_path: Path, *, stage: str) -> None:
        """Render final textual report summary for one trap run."""
        summary = load_security_summary(run_manifest_path)
        view = build_final_summary_view(summary)
        print()
        if (
            stage in {"run", "eval"}
            and summary.security_status == "unavailable"
            and summary.scored_cases == 0
        ):
            print("Evaluation")
            print("⚠ Skipped  no cases were evaluated")
            print()
        if stage in {"run", "generate", "execute"}:
            print("Cases")
            _print_plain_rows(view.cases_rows)
            print()
        if stage in {"run", "execute"}:
            print("Case Execution")
            _print_plain_rows(view.execution_rows)
            print()
        if stage in {"run", "eval"}:
            print("Trap Evaluation")
            _print_plain_rows(view.evaluation_rows)
        if self.verbose:
            print()
            print("Artifacts")
            _print_plain_rows(artifact_rows(run_manifest_path))

    def _print_run_header(self) -> None:
        title = self._state.stage.title() if self._state.stage else "Run"
        print(f"OpenTrap {title}")
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
            print(f"evaluation.progress: {processed}/{total} ({percent:.1f}%)", file=sys.stderr)

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


def _print_plain_rows(rows: list[tuple[str, str]]) -> None:
    label_width = max((len(label) for label, _value in rows), default=0)
    for label, value in rows:
        print(f"{label:<{label_width}}  {value}")


def _int_or_default(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default
