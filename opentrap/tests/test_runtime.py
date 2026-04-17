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
