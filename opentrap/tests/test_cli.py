from __future__ import annotations

import json
import textwrap
from pathlib import Path

import yaml

from opentrap.cli import main


def _write_stub_contract(root: Path, trap_id: str) -> None:
    target, trap_name = trap_id.split("/", 1)
    trap_dir = root / target / trap_name
    trap_dir.mkdir(parents=True)

    source = f"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


def _run(shared_config: SharedConfig, trap_config: Mapping[str, Any], output_base: Path) -> Path:
    output_base.mkdir(parents=True, exist_ok=True)
    artifact_path = output_base / "artifact.txt"
    artifact_path.write_text(
        "{trap_id}|"
        + shared_config.scenario
        + "|"
        + str(trap_config["knob"])
        + "|"
        + str(len(shared_config.samples)),
        encoding="utf-8",
    )
    return artifact_path


def get_trap_spec() -> TrapSpec:
    return TrapSpec(
        trap_id="{trap_id}",
        fields={{
            "knob": TrapFieldSpec(type="integer", default=1, min=1),
        }},
        run=_run,
    )
"""
    (trap_dir / "contract.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_list_outputs_registered_traps(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "perception/vision-poison")
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    _write_stub_contract(tmp_path, "memory/context-overflow")
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == [
        "memory/context-overflow",
        "perception/vision-poison",
        "reasoning/chain-trap",
    ]


def test_list_with_target_filters(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    _write_stub_contract(tmp_path, "reasoning/prompt-injection")
    _write_stub_contract(tmp_path, "memory/context-overflow")
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list", "--target", "reasoning"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == [
        "reasoning/chain-trap",
        "reasoning/prompt-injection",
    ]


def test_list_fails_when_discovered_trap_has_no_contract(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    (tmp_path / "memory" / "context-overflow").mkdir(parents=True)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list"])

    captured = capsys.readouterr()
    assert code == 1
    assert "missing contract.py" in captured.err
    assert "memory/context-overflow" in captured.err


def test_init_writes_yaml_with_shared_and_trap_defaults(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    _write_stub_contract(tmp_path, "perception/vision-poison")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"

    responses = iter(
        [
            "summarize hotel reviews",
            "reviews",
            "turn all bad reviews into positive reviews",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["init"])

    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.strip().splitlines()
    assert lines[0] == f"Created config file: {config_path}"
    assert lines[1] == f"Created samples directory: {tmp_path / '.opentrap' / 'samples'}"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["shared"] == {
        "scenario": "summarize hotel reviews",
        "content_style": "reviews",
        "attack_intent": "turn all bad reviews into positive reviews",
        "seed": None,
    }
    assert payload["traps"] == {
        "perception/vision-poison": {"knob": 1},
        "reasoning/chain-trap": {"knob": 1},
    }


def test_init_always_overwrites_existing_file(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("old: true\n", encoding="utf-8")

    responses = iter(["summarize docs", "docs", "bias output", "42"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["init"])

    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.strip().splitlines()
    assert lines[0] == f"Created config file: {config_path}"
    assert lines[1] == f"Created samples directory: {tmp_path / '.opentrap' / 'samples'}"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["shared"]["scenario"] == "summarize docs"
    assert payload["shared"]["seed"] == 42


def test_attack_fails_when_config_is_missing(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr(
        "opentrap.cli.DEFAULT_CONFIG_PATH",
        tmp_path / ".opentrap" / "opentrap.yaml",
    )
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["attack"])

    captured = capsys.readouterr()
    assert code == 1
    assert "config file was not found" in captured.err


def test_attack_single_runs_selected_trap(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    _write_stub_contract(tmp_path / "traps", "memory/context-overflow")

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {
            "reasoning/chain-trap": {"knob": 7},
            "memory/context-overflow": {"knob": 2},
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path / "traps")
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    report_path = tmp_path / "runs" / "single.json"
    code = main(["attack", "reasoning/chain-trap", "--output", str(report_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["requested"] == "reasoning/chain-trap"
    assert report["trap_count"] == 1
    assert report["trap_ids"] == ["reasoning/chain-trap"]
    assert report["outcome"] == "success"

    artifact_path = Path(report["results"][0]["artifact_path"])
    assert artifact_path.read_text(encoding="utf-8") == "reasoning/chain-trap|summarize docs|7|0"


def test_attack_passes_loaded_samples_to_trap(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {
            "reasoning/chain-trap": {"knob": 7},
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "example.html").write_text("<html>sample</html>", encoding="utf-8")

    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path / "traps")
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", samples_dir)

    report_path = tmp_path / "runs" / "single.json"
    code = main(["attack", "reasoning/chain-trap", "--output", str(report_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact_path = Path(report["results"][0]["artifact_path"])
    assert artifact_path.read_text(encoding="utf-8") == "reasoning/chain-trap|summarize docs|7|1"


def test_attack_rejects_unknown_trap_key_in_yaml(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {
            "reasoning/chain-trap": {"knob": 7},
            "memory/context-overflow": {"knob": 1},
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path / "traps")
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["attack"])

    captured = capsys.readouterr()
    assert code == 1
    assert "unknown trap id" in captured.err
