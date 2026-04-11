from __future__ import annotations

import json
from pathlib import Path

from opentrap.cli import main


def _make_scenario_tree(root: Path) -> None:
    (root / "perception" / "vision-poison").mkdir(parents=True)
    (root / "reasoning" / "chain-trap").mkdir(parents=True)
    (root / "memory" / "context-overflow").mkdir(parents=True)


def test_list_outputs_all_scenarios(capsys, tmp_path: Path, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == [
        "memory/context-overflow",
        "perception/vision-poison",
        "reasoning/chain-trap",
    ]


def test_list_discovers_lowercase_target_directories(capsys, tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "reasoning" / "prompt_injection_via_html").mkdir(parents=True)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == ["reasoning/prompt_injection_via_html"]


def test_list_with_target_filters(capsys, tmp_path: Path, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list", "--target", "reasoning"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == ["reasoning/chain-trap"]


def test_attack_single_generates_report_file(tmp_path: Path, capsys, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    output = tmp_path / "runs" / "run.json"

    code = main(
        [
            "attack",
            "reasoning/chain-trap",
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "v1"
    assert payload["requested"] == "reasoning/chain-trap"
    assert payload["scenario_count"] == 1
    assert payload["scenario_ids"] == ["reasoning/chain-trap"]
    assert payload["outcome"] == "unknown"


def test_attack_all_runs_all_scenarios(tmp_path: Path, capsys, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    output = tmp_path / "runs" / "all.json"
    code = main(["attack", "--output", str(output)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["requested"] is None
    assert payload["scenario_count"] == 3
    assert payload["scenario_ids"] == [
        "memory/context-overflow",
        "perception/vision-poison",
        "reasoning/chain-trap",
    ]


def test_attack_fails_when_scenario_is_missing(tmp_path: Path, capsys, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    code = main(["attack", "memory/does-not-exist"])

    captured = capsys.readouterr()
    assert code == 1
    assert "was not found" in captured.err


def test_attack_requires_target_and_name_format(tmp_path: Path, capsys, monkeypatch) -> None:
    _make_scenario_tree(tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    code = main(["attack", "chain-trap"])
    captured = capsys.readouterr()
    assert code == 1
    assert "target/name" in captured.err
