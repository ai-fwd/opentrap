from __future__ import annotations

from pathlib import Path

from opentrap.cli_rendering.rich_renderer import RichRenderer
from opentrap.events import RunEvent


def test_rich_renderer_smoke_sequence_starts_and_stops_live(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "abc"
    run_dir.mkdir(parents=True)
    run_manifest_path = run_dir / "run.json"
    run_manifest_path.write_text("{}", encoding="utf-8")

    renderer = RichRenderer(verbose=False)
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
    assert renderer._live is not None

    renderer(
        RunEvent(
            type="run_failed",
            payload={"stage": "run", "error": "boom"},
        )
    )
    assert renderer._live is None
