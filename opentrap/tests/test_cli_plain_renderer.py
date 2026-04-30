from __future__ import annotations

import json
from pathlib import Path

from opentrap.cli_rendering.plain_renderer import PlainRenderer
from opentrap.events import RunEvent


def test_plain_renderer_normal_run_path_outputs_expected_sections(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs" / "abc"
    run_dir.mkdir(parents=True)
    run_manifest_path = run_dir / "run.json"
    run_manifest_path.write_text("{}", encoding="utf-8")
    _write_report(run_dir)

    counts = {
        "generated_artifacts": 1,
        "scenario_cases": 1,
        "base_cases": 1,
        "variant_cases": 0,
        "selected_cases": 1,
        "harness_executed": 1,
        "harness_passed": 1,
        "harness_failed": 0,
        "scored_cases": 1,
        "trap_successes": 0,
    }

    renderer = PlainRenderer(verbose=False)
    renderer(
        RunEvent(
            type="run_started",
            payload={
                "trap_id": "reasoning/chain-trap",
                "target": "acme-client",
                "harness_command": "bun test",
                "run_dir": str(run_dir),
                "run_manifest_path": str(run_manifest_path),
            },
        )
    )
    renderer(RunEvent(type="generate_started", payload={}))
    renderer(RunEvent(type="generate_completed", payload={"counts": counts}))
    renderer(RunEvent(type="adapter_ready", payload={"host": "127.0.0.1", "port": 7860}))
    renderer(RunEvent(type="run_finalized", payload={"counts": counts}))
    renderer(
        RunEvent(
            type="evaluate_completed",
            payload={"run_manifest_path": str(run_manifest_path)},
        )
    )

    captured = capsys.readouterr()
    assert "OpenTrap Run" in captured.out
    assert "Trap:      reasoning/chain-trap" in captured.out
    assert "✓ Dataset generated" in captured.out
    assert "✓ Adapter ready (127.0.0.1:7860)" in captured.out
    assert "✓ Harness completed" in captured.out
    assert "✓ Evaluation completed" in captured.out
    assert "Cases" in captured.out
    assert "Case Execution" in captured.out
    assert "Trap Evaluation" in captured.out


def _write_report(run_dir: Path) -> None:
    payload = {
        "run_id": "run-1",
        "counts": {
            "generated_artifacts": 1,
            "scenario_cases": 1,
            "base_cases": 1,
            "variant_cases": 0,
            "selected_cases": 1,
            "harness_executed": 1,
            "harness_passed": 1,
            "harness_failed": 0,
            "scored_cases": 1,
            "trap_successes": 0,
        },
        "security_result": {
            "status": "no_successful_traps_detected",
        },
    }
    (run_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")
