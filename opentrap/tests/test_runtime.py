# OpenTrap runtime API tests.
# Verifies session lifecycle, data item resolution, event emission, and finalization artifacts.
from __future__ import annotations

import json
from pathlib import Path

import opentrap.runtime as runtime_module


def test_start_session_resolves_repo_relative_data_items_against_manifest_repo_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    data_file = repo_root / ".opentrap" / "dataset" / "item-00001.txt"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("dataset payload", encoding="utf-8")

    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "run.json"
    manifest = {
        "run_id": "test-run-id",
        "repo_root": str(repo_root),
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "reasoning/chain-trap",
                "data_items": [
                    {
                        "id": "00001",
                        "path": ".opentrap/dataset/item-00001.txt",
                    }
                ],
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    previous_session = runtime_module._active_session
    runtime_module._active_session = None
    try:
        runtime_module.start_session(manifest_path)
        item = runtime_module.get_data_item("00001")
        assert Path(item.path) == data_file
    finally:
        runtime_module._active_session = None
        if previous_session is not None:
            runtime_module._active_session = previous_session


def test_runtime_session_and_report_payloads_do_not_include_event_count(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-2"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "run.json"
    manifest = {
        "run_id": "runtime-run-id",
        "repo_root": str(tmp_path),
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "reasoning/chain-trap",
                "data_items": [],
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    previous_session = runtime_module._active_session
    runtime_module._active_session = None
    try:
        runtime_module.start_session(manifest_path)
        runtime_module.emit_event("dummy", {"ok": True})
        runtime_module.end_session()
    finally:
        runtime_module._active_session = None
        if previous_session is not None:
            runtime_module._active_session = previous_session

    sessions_path = run_dir / "sessions.jsonl"
    sessions = [
        json.loads(line)
        for line in sessions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(sessions) == 1
    assert "event_count" not in sessions[0]

    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "event_count" not in report
    assert "events_by_type" not in report
