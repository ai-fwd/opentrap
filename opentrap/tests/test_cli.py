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


def _write_stub_adapter(path: Path) -> None:
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


def _configure_trap_run_paths(
    *,
    monkeypatch,
    tmp_path: Path,
    config_path: Path,
    samples_dir: Path,
    adapter_path: Path,
) -> None:
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path / "traps")
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("opentrap.cli.DEFAULT_DATASET_DIR", tmp_path / ".opentrap" / "dataset")
    monkeypatch.setattr("opentrap.cli.DEFAULT_ADAPTER_ENTRYPOINT", adapter_path)


def _base_payload(*, trap_intent: str = "rewrite negatives", knob: int = 7) -> dict:
    return {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": trap_intent,
            "seed": None,
        },
        "traps": {
            "reasoning/chain-trap": {"knob": knob},
        },
    }


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
        "trap_intent": "turn all bad reviews into positive reviews",
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


def test_trap_run_fails_when_config_is_missing(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr(
        "opentrap.cli.DEFAULT_CONFIG_PATH",
        tmp_path / ".opentrap" / "opentrap.yaml",
    )
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 1
    assert "config file was not found" in captured.err


def test_trap_run_single_runs_selected_trap(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    _write_stub_contract(tmp_path / "traps", "memory/context-overflow")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter(adapter_path)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _base_payload()
    payload["traps"]["memory/context-overflow"] = {"knob": 2}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=tmp_path / ".opentrap" / "samples",
        adapter_path=adapter_path,
    )

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = Path(captured.out.strip())
    assert run_manifest_path.exists()

    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["requested"] == "reasoning/chain-trap"
    assert run_manifest["trap_count"] == 1
    assert run_manifest["status"] == "ready"
    assert len(run_manifest["traps"]) == 1
    assert run_manifest["traps"][0]["trap_id"] == "reasoning/chain-trap"
    assert run_manifest["traps"][0]["dataset_fingerprint"]
    assert run_manifest["traps"][0]["dataset_cache_dir"]
    assert run_manifest["traps"][0]["dataset_source"] == "generated_then_cached"
    assert run_manifest["active_session_id"]

    artifact_path = Path(run_manifest["traps"][0]["artifact_path"])
    assert artifact_path.read_text(encoding="utf-8") == "reasoning/chain-trap|summarize docs|7|0"
    run_dir = run_manifest_path.parent
    assert (run_dir / f"session-{run_manifest['active_session_id']}.json").exists()
    assert not (run_dir / "report.json").exists()


def test_trap_run_passes_loaded_samples_to_trap(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter(adapter_path)
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _base_payload()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "example.html").write_text("<html>sample</html>", encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        adapter_path=adapter_path,
    )

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = Path(captured.out.strip())
    assert run_manifest_path.exists()

    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    artifact_path = Path(run_manifest["traps"][0]["artifact_path"])
    assert artifact_path.read_text(encoding="utf-8") == "reasoning/chain-trap|summarize docs|7|1"


def test_trap_run_rejects_unknown_trap_key_in_yaml(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
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

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 1
    assert "unknown trap id" in captured.err


def test_trap_run_reuses_dataset_when_config_is_unchanged(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter(adapter_path)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        adapter_path=adapter_path,
    )

    code1 = main(["reasoning/chain-trap"])
    captured1 = capsys.readouterr()
    assert code1 == 0
    run_manifest_path_1 = Path(captured1.out.strip())
    run_1 = json.loads(run_manifest_path_1.read_text(encoding="utf-8"))
    trap_1 = run_1["traps"][0]

    code2 = main(["reasoning/chain-trap"])
    captured2 = capsys.readouterr()
    assert code2 == 0
    run_manifest_path_2 = Path(captured2.out.strip())
    run_2 = json.loads(run_manifest_path_2.read_text(encoding="utf-8"))
    trap_2 = run_2["traps"][0]

    assert run_1["run_id"] != run_2["run_id"]
    assert trap_1["dataset_source"] == "generated_then_cached"
    assert trap_2["dataset_source"] == "cache_hit"
    assert trap_1["dataset_fingerprint"] == trap_2["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] == trap_2["dataset_cache_dir"]
    assert trap_1["artifact_path"] == trap_2["artifact_path"]


def test_trap_run_regenerates_dataset_when_shared_or_trap_config_changes(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter(adapter_path)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        adapter_path=adapter_path,
    )

    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    code1 = main(["reasoning/chain-trap"])
    captured1 = capsys.readouterr()
    assert code1 == 0
    run_1 = json.loads(Path(captured1.out.strip()).read_text(encoding="utf-8"))
    trap_1 = run_1["traps"][0]

    payload_changed_shared = _base_payload(trap_intent="new trap intent")
    config_path.write_text(
        yaml.safe_dump(payload_changed_shared, sort_keys=False),
        encoding="utf-8",
    )
    code2 = main(["reasoning/chain-trap"])
    captured2 = capsys.readouterr()
    assert code2 == 0
    run_2 = json.loads(Path(captured2.out.strip()).read_text(encoding="utf-8"))
    trap_2 = run_2["traps"][0]

    payload_changed_trap = _base_payload(knob=99)
    config_path.write_text(yaml.safe_dump(payload_changed_trap, sort_keys=False), encoding="utf-8")
    code3 = main(["reasoning/chain-trap"])
    captured3 = capsys.readouterr()
    assert code3 == 0
    run_3 = json.loads(Path(captured3.out.strip()).read_text(encoding="utf-8"))
    trap_3 = run_3["traps"][0]

    assert trap_1["dataset_fingerprint"] != trap_2["dataset_fingerprint"]
    assert trap_2["dataset_fingerprint"] != trap_3["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] != trap_2["dataset_cache_dir"]
    assert trap_2["dataset_cache_dir"] != trap_3["dataset_cache_dir"]


def test_trap_run_regenerates_dataset_when_samples_change(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter(adapter_path)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)
    sample_file = samples_dir / "example.html"
    sample_file.write_text("<html>one</html>", encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        adapter_path=adapter_path,
    )

    code1 = main(["reasoning/chain-trap"])
    captured1 = capsys.readouterr()
    assert code1 == 0
    run_1 = json.loads(Path(captured1.out.strip()).read_text(encoding="utf-8"))
    trap_1 = run_1["traps"][0]

    sample_file.write_text("<html>two</html>", encoding="utf-8")
    code2 = main(["reasoning/chain-trap"])
    captured2 = capsys.readouterr()
    assert code2 == 0
    run_2 = json.loads(Path(captured2.out.strip()).read_text(encoding="utf-8"))
    trap_2 = run_2["traps"][0]

    assert trap_1["dataset_fingerprint"] != trap_2["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] != trap_2["dataset_cache_dir"]
