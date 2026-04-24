# OpenTrap CLI tests.
# Verifies trap run orchestration, manifest lifecycle, and config validation.
# Also covers adapter process shutdown handling.
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from opentrap.cli import main


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_stub_contract(root: Path, trap_id: str) -> None:
    _write_stub_contract_with_behavior(root, trap_id)


def _write_stub_contract_with_behavior(
    root: Path,
    trap_id: str,
    *,
    module_prelude: str = "",
    init_body: str = "pass",
) -> None:
    target, trap_name = trap_id.split("/", 1)
    trap_dir = root / target / trap_name
    trap_dir.mkdir(parents=True)

    source = f"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from opentrap.trap_contract import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

{module_prelude}

class Trap(TrapSpec[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]):
    trap_id = ""
    fields = {{
        "knob": TrapFieldSpec(type="integer", default=1, min=1),
    }}

    def __init__(self) -> None:
        {init_body}

    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        run_dir = output_base / "artifact"
        data_dir = run_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = data_dir / "00001.txt"
        artifact_path.write_text(
            "{trap_id}|"
            + shared_config.scenario
            + "|"
            + str(trap_config["knob"])
            + "|"
            + str(len(shared_config.samples)),
            encoding="utf-8",
        )
        metadata_path = run_dir / "metadata.jsonl"
        metadata_path.write_text(
            json.dumps({{"file_id": "00001", "filename": "00001.txt"}}) + "\\n",
            encoding="utf-8",
        )
        return run_dir

    def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(context)

    def build_cases(self, context: TrapCaseContext) -> list[dict[str, Any]]:
        return [
            {{
                "item_id": "00001",
                "data_item": {{
                    "id": "00001",
                    "path": str(context.data_dir / "00001.txt"),
                }},
                "metadata": {{"file_id": "00001", "filename": "00001.txt"}},
            }}
        ]

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return {{"score": 0.0, "context": dict(context)}}
"""
    (trap_dir / "trap.py").write_text(textwrap.dedent(source), encoding="utf-8")


def _write_generated_adapter(
    generated_root: Path,
    *,
    product: str = "default",
    handlers_prelude: str = "",
) -> None:
    generated_dir = generated_root / product
    generated_dir.mkdir(parents=True, exist_ok=True)

    handlers_source = f"""
from __future__ import annotations

{handlers_prelude}
"""

    (generated_dir / "handlers.py").write_text(textwrap.dedent(handlers_source), encoding="utf-8")
    (generated_dir / "adapter.yaml").write_text("routes: []\nupstreams: {}\n", encoding="utf-8")


def _configure_trap_run_paths(
    *,
    monkeypatch,
    tmp_path: Path,
    config_path: Path,
    samples_dir: Path,
    generated_root: Path,
) -> None:
    monkeypatch.setattr("opentrap.cli.DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path / "traps")
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("opentrap.cli.DEFAULT_DATASET_DIR", tmp_path / ".opentrap" / "dataset")
    monkeypatch.setattr("opentrap.cli.DEFAULT_ADAPTER_GENERATED_ROOT", generated_root)


def _base_payload(*, trap_intent: str = "rewrite negatives", knob: int = 7) -> dict:
    return {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": trap_intent,
            "seed": None,
        },
        "harness": {
            "command": [sys.executable, "-c", "pass"],
            "cwd": ".",
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


def test_list_is_discovery_only_and_does_not_import_modules(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract_with_behavior(
        tmp_path,
        "reasoning/chain-trap",
        module_prelude='raise RuntimeError("trap module imported unexpectedly")',
    )
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)

    code = main(["list"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip().splitlines() == ["reasoning/chain-trap"]


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
    assert "missing trap.py" in captured.err
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
            "bunx playwright test",
            "acme-client",
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
    assert payload["harness"] == {
        "command": ["bunx", "playwright", "test"],
        "cwd": "acme-client",
    }


def test_init_prompts_for_harness_and_parses_command_tokens(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    prompts: list[str] = []
    responses = iter(
        [
            "summarize docs",
            "docs",
            "bias output",
            "",
            'bunx playwright test --grep "critical path"',
            "acme-client",
        ]
    )

    def _fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["init"])

    assert code == 0
    assert prompts == [
        "Scenario: ",
        "Content style: ",
        "Trap intent: ",
        "Seed (optional integer): ",
        "What command runs your test suite? (e.g. bunx playwright test): ",
        "Where should this command be run? (relative path, e.g. acme-client): ",
    ]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["harness"] == {
        "command": ["bunx", "playwright", "test", "--grep", "critical path"],
        "cwd": "acme-client",
    }
    capsys.readouterr()


def test_init_retries_harness_prompts_until_valid(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    responses = iter(
        [
            "summarize docs",
            "docs",
            "bias output",
            "",
            "",
            "bunx playwright test",
            "",
            "/absolute/path",
            "acme-client",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["init"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Command cannot be empty." in captured.err
    assert "Path cannot be empty." in captured.err
    assert "Path must be relative." in captured.err


def test_init_always_overwrites_existing_file(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path, "reasoning/chain-trap")
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("old: true\n", encoding="utf-8")

    responses = iter(
        [
            "summarize docs",
            "docs",
            "bias output",
            "42",
            "bunx playwright test",
            "acme-client",
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
    assert payload["shared"]["scenario"] == "summarize docs"
    assert payload["shared"]["seed"] == 42
    assert payload["harness"]["cwd"] == "acme-client"


def test_init_does_not_instantiate_trap_classes(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract_with_behavior(
        tmp_path,
        "reasoning/chain-trap",
        init_body='raise RuntimeError("constructor should not run during init")',
    )
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"

    responses = iter(
        [
            "summarize docs",
            "docs",
            "bias output",
            "",
            "bunx playwright test",
            "acme-client",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", tmp_path / ".opentrap" / "samples")

    code = main(["init"])

    captured = capsys.readouterr()
    assert code == 0
    assert f"Created config file: {config_path}" in captured.out


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


@pytest.mark.parametrize(
    ("sample_content", "expected_sample_count"),
    [
        (None, 0),
        ("<html>sample</html>", 1),
    ],
    ids=["no-samples", "one-sample"],
)
def test_trap_run_single_records_manifest_and_artifact(
    tmp_path: Path,
    capsys,
    monkeypatch,
    sample_content: str | None,
    expected_sample_count: int,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    _write_stub_contract(tmp_path / "traps", "memory/context-overflow")
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _base_payload()
    payload["traps"]["memory/context-overflow"] = {"knob": 2}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    if sample_content is not None:
        (samples_dir / "example.html").write_text(sample_content, encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = Path(captured.out.strip())
    assert run_manifest_path.exists()

    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["requested"] == "reasoning/chain-trap"
    assert run_manifest["trap_count"] == 1
    assert run_manifest["status"] == "finalized"
    assert len(run_manifest["traps"]) == 1
    assert run_manifest["traps"][0]["trap_id"] == "reasoning/chain-trap"
    assert run_manifest["traps"][0]["dataset_fingerprint"]
    assert run_manifest["traps"][0]["dataset_cache_dir"]
    assert run_manifest["traps"][0]["dataset_source"] == "generated_then_cached"
    assert run_manifest["active_session_id"] is None

    artifact_path = Path(run_manifest["traps"][0]["artifact_path"])
    assert (artifact_path / "data" / "00001.txt").read_text(encoding="utf-8") == (
        f"reasoning/chain-trap|summarize docs|7|{expected_sample_count}"
    )
    run_dir = run_manifest_path.parent
    session_id = run_manifest["sessions"][0]["session_id"]
    assert (run_dir / f"session-{session_id}.json").exists()
    assert (run_dir / "report.json").exists()


def test_trap_run_instantiates_only_selected_trap(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    _write_stub_contract_with_behavior(
        tmp_path / "traps",
        "memory/context-overflow",
        init_body='raise RuntimeError("non-selected constructor should not run")',
    )
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

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
        generated_root=generated_root,
    )

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = Path(captured.out.strip())
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["traps"][0]["trap_id"] == "reasoning/chain-trap"


def test_trap_run_returns_failure_when_harness_case_fails(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _base_payload()
    payload["harness"]["command"] = [sys.executable, "-c", "raise SystemExit(3)"]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=tmp_path / ".opentrap" / "samples",
        generated_root=generated_root,
    )

    code = main(["reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 1
    run_manifest_path = Path(captured.out.strip())
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["status"] == "finalized"
    assert run_manifest["succeeded"] is False
    assert run_manifest["sessions"][0]["harness_exit_code"] == 3


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
        "harness": {
            "command": ["bunx", "playwright", "test"],
            "cwd": "acme-client",
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
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

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
        generated_root=generated_root,
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
    assert Path(trap_1["artifact_path"]) == Path(trap_1["dataset_cache_dir"])
