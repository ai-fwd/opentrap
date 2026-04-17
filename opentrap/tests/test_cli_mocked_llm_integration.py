"""CLI integration coverage for cache/orchestration using deterministic mocked LLM output.

These tests execute real trap runs through `opentrap.cli.main(...)` while injecting a fake
`openai` module so behavior is reproducible and network-independent.
"""

from __future__ import annotations

import json
import sys
import textwrap
import types
from pathlib import Path

import yaml

from opentrap.cli import main

TRAP_ID = "perception/prompt_injection_via_html"


def _repo_root() -> Path:
    """Return repository root from the tests directory."""
    return Path(__file__).resolve().parents[2]


def _traps_root() -> Path:
    """Return the repository trap root used by real integration runs."""
    return _repo_root() / "opentrap" / "src" / "traps"


def _base_payload(*, trap_intent: str = "rewrite negatives", base_count: int = 1) -> dict:
    """Build minimal config payload for the real perception trap."""
    return {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": trap_intent,
            "seed": 42,
        },
        "traps": {
            TRAP_ID: {
                "location_temperature": 0.0,
                "density_temperature": 0.0,
                "diversity_temperature": 0.0,
                "base_count": base_count,
            },
        },
    }


def _write_adapter_start_session(path: Path) -> None:
    """Write adapter script that immediately starts a runtime session."""
    source = """
from __future__ import annotations

import argparse

from opentrap.runtime import start_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    start_session(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _write_adapter_exit_immediately(path: Path) -> None:
    """Write adapter script that exits before setting `active_session_id`."""
    source = """
from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _write_adapter_sleep(path: Path, *, seconds: float) -> None:
    """Write adapter script that blocks long enough to trigger startup timeout."""
    source = f"""
from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.parse_args()
    time.sleep({seconds})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _configure_cli_paths(
    *,
    monkeypatch,
    tmp_path: Path,
    config_path: Path,
    samples_dir: Path,
    adapter_path: Path,
) -> None:
    """Point CLI path defaults to isolated test directories."""
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", _traps_root())
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("opentrap.cli.DEFAULT_DATASET_DIR", tmp_path / ".opentrap" / "dataset")
    monkeypatch.setattr("opentrap.cli.DEFAULT_ADAPTER_ENTRYPOINT", adapter_path)


def _install_fake_openai(monkeypatch) -> None:
    """Install deterministic fake OpenAI client used by trap generator code."""

    class _FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN003
            del kwargs
            message = types.SimpleNamespace(
                content=(
                    "<!DOCTYPE html><html><head><title>Mocked</title></head>"
                    "<body><h1>Example</h1><p>Body</p></body></html>"
                )
            )
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class _FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            del kwargs
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)


def _read_trap_entry(run_manifest_path: Path) -> dict:
    """Load first trap entry from a run manifest file."""
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    return run_manifest["traps"][0]


def _prepare_llm_trap_run(
    *,
    monkeypatch,
    tmp_path: Path,
    adapter_path: Path,
    payload: dict,
) -> tuple[Path, Path]:
    """Prepare config, env, and deterministic fake openai for integration run."""
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "mocked-model")

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    _configure_cli_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        adapter_path=adapter_path,
    )
    return config_path, samples_dir


def test_llm_mocked_run_reuses_dataset_when_inputs_are_unchanged(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure cache hit behavior for identical trap inputs across repeated runs."""
    adapter_path = tmp_path / "adapter-main.py"
    _write_adapter_start_session(adapter_path)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )

    code1 = main([TRAP_ID])
    captured1 = capsys.readouterr()
    assert code1 == 0
    run_manifest_path_1 = Path(captured1.out.strip())
    trap_1 = _read_trap_entry(run_manifest_path_1)

    code2 = main([TRAP_ID])
    captured2 = capsys.readouterr()
    assert code2 == 0
    run_manifest_path_2 = Path(captured2.out.strip())
    trap_2 = _read_trap_entry(run_manifest_path_2)

    assert trap_1["dataset_source"] == "generated_then_cached"
    assert trap_2["dataset_source"] == "cache_hit"
    assert trap_1["dataset_fingerprint"] == trap_2["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] == trap_2["dataset_cache_dir"]


def test_llm_mocked_run_uses_final_cache_paths_for_manifest_data_items(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure manifest data item paths point at the finalized cache artifact, not staging."""
    adapter_path = tmp_path / "adapter-main.py"
    _write_adapter_start_session(adapter_path)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )

    code1 = main([TRAP_ID])
    captured1 = capsys.readouterr()
    assert code1 == 0
    trap_1 = _read_trap_entry(Path(captured1.out.strip()))

    code2 = main([TRAP_ID])
    captured2 = capsys.readouterr()
    assert code2 == 0
    trap_2 = _read_trap_entry(Path(captured2.out.strip()))

    for trap in (trap_1, trap_2):
        data_dir = Path(trap["data_dir"])
        assert Path(trap["artifact_path"]) == Path(trap["dataset_cache_dir"])
        assert data_dir == Path(trap["dataset_cache_dir"]) / "data"
        assert trap["data_items"]
        for item in trap["data_items"]:
            assert "_tmp" not in item["path"]
            assert Path(item["path"]).parent == data_dir


def test_llm_mocked_run_regenerates_when_shared_or_trap_config_changes(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure fingerprint invalidation when shared or trap config inputs change."""
    adapter_path = tmp_path / "adapter-main.py"
    _write_adapter_start_session(adapter_path)
    config_path, _samples_dir = _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )

    code1 = main([TRAP_ID])
    captured1 = capsys.readouterr()
    assert code1 == 0
    trap_1 = _read_trap_entry(Path(captured1.out.strip()))

    config_path.write_text(
        yaml.safe_dump(_base_payload(trap_intent="changed intent"), sort_keys=False),
        encoding="utf-8",
    )
    code2 = main([TRAP_ID])
    captured2 = capsys.readouterr()
    assert code2 == 0
    trap_2 = _read_trap_entry(Path(captured2.out.strip()))

    changed_payload = _base_payload()
    changed_payload["traps"][TRAP_ID]["base_count"] = 2
    config_path.write_text(yaml.safe_dump(changed_payload, sort_keys=False), encoding="utf-8")
    code3 = main([TRAP_ID])
    captured3 = capsys.readouterr()
    assert code3 == 0
    trap_3 = _read_trap_entry(Path(captured3.out.strip()))

    assert trap_1["dataset_fingerprint"] != trap_2["dataset_fingerprint"]
    assert trap_2["dataset_fingerprint"] != trap_3["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] != trap_2["dataset_cache_dir"]
    assert trap_2["dataset_cache_dir"] != trap_3["dataset_cache_dir"]


def test_llm_mocked_run_regenerates_when_samples_change(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure sample boundary content contributes to dataset fingerprint identity."""
    adapter_path = tmp_path / "adapter-main.py"
    _write_adapter_start_session(adapter_path)
    _config_path, samples_dir = _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )
    sample_file = samples_dir / "example.html"
    sample_file.write_text("<html>one</html>", encoding="utf-8")

    code1 = main([TRAP_ID])
    captured1 = capsys.readouterr()
    assert code1 == 0
    trap_1 = _read_trap_entry(Path(captured1.out.strip()))

    sample_file.write_text("<html>two</html>", encoding="utf-8")
    code2 = main([TRAP_ID])
    captured2 = capsys.readouterr()
    assert code2 == 0
    trap_2 = _read_trap_entry(Path(captured2.out.strip()))

    assert trap_1["dataset_fingerprint"] != trap_2["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] != trap_2["dataset_cache_dir"]


def test_trap_run_fails_when_adapter_entrypoint_is_missing(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Surface explicit adapter-path failure for orchestration troubleshooting."""
    missing_adapter = tmp_path / "missing-adapter.py"
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=missing_adapter,
        payload=_base_payload(),
    )

    code = main([TRAP_ID])

    captured = capsys.readouterr()
    assert code == 1
    assert "adapter entrypoint was not found" in captured.err


def test_trap_run_fails_when_adapter_exits_before_session_start(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Surface early adapter exit failures before runtime session handshake."""
    adapter_path = tmp_path / "adapter-exit.py"
    _write_adapter_exit_immediately(adapter_path)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )

    code = main([TRAP_ID])

    captured = capsys.readouterr()
    assert code == 1
    assert "adapter exited before session start" in captured.err


def test_trap_run_fails_when_adapter_does_not_start_session_before_timeout(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Surface adapter startup timeout when session id is never published."""
    adapter_path = tmp_path / "adapter-sleep.py"
    _write_adapter_sleep(adapter_path, seconds=1.0)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        adapter_path=adapter_path,
        payload=_base_payload(),
    )
    monkeypatch.setattr("opentrap.run_orchestration.SESSION_START_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr("opentrap.run_orchestration.SESSION_POLL_INTERVAL_SECONDS", 0.01)

    code = main([TRAP_ID])

    captured = capsys.readouterr()
    assert code == 1
    assert "timed out waiting for adapter session start" in captured.err
