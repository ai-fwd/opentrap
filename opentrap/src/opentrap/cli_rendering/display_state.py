"""Shared CLI display state and summary loading helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from opentrap.io_utils import load_json_maybe


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
class RunDisplayState:
    stage: str = "run"
    trap_id: str = "-"
    target: str = "-"
    harness_command: str = "-"
    run_dir: str = "-"
    run_manifest_path: str = ""
    max_cases: int | None = None
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
    generated_artifacts = required_int(counts, "generated_artifacts")
    scenario_cases = required_int(counts, "scenario_cases")
    base_cases = required_int(counts, "base_cases")
    variant_cases = required_int(counts, "variant_cases")
    selected_cases = required_int(counts, "selected_cases")
    harness_executed = required_int(counts, "harness_executed")
    harness_passed = required_int(counts, "harness_passed")
    harness_failed = required_int(counts, "harness_failed")
    scored_cases = required_int(counts, "scored_cases")
    trap_successes = required_int(counts, "trap_successes")
    rate_percent = format_percent((trap_successes / scored_cases) if scored_cases > 0 else 0.0)

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
        report_path=str(preferred_report_artifact_path(run_manifest_path)),
    )


def path_from_payload(payload: Mapping[str, object], key: str) -> Path | None:
    raw = payload.get(key)
    if isinstance(raw, str) and raw:
        return Path(raw)
    return None


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def adapter_endpoint(payload: Mapping[str, object]) -> str | None:
    host = payload.get("host")
    port = payload.get("port")
    if isinstance(host, str) and host and isinstance(port, int):
        return f"{host}:{port}"
    if isinstance(port, int):
        return str(port)
    return None


def preferred_report_artifact_path(run_manifest_path: Path) -> Path:
    html_report_path = run_manifest_path.parent / "evaluation_report.html"
    if html_report_path.exists():
        return html_report_path
    return run_manifest_path.parent / "evaluation.csv"


def int_or_default(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def format_percent(value: object) -> str:
    if isinstance(value, int | float):
        return f"{float(value) * 100.0:.1f}%"
    return "0.0%"


def required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    raise RuntimeError(f"missing required integer field: {key}")


def require_counts_payload(payload: Mapping[str, object]) -> dict[str, int]:
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
        counts[key] = required_int(raw_counts, key)
    return counts
