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
    evaluate_body: str = (
        "return EvaluationResult(success_count=0, evaluated_count=1, details=None)"
    ),
) -> None:
    target, trap_name = trap_id.split("/", 1)
    trap_dir = root / target / trap_name
    trap_dir.mkdir(parents=True)

    source = f"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from opentrap.evaluation import EvaluationResult
from opentrap.trap import (
    SharedConfig,
    TrapCaseContext,
    TrapFieldSpec,
    TrapGenerationCounts,
    TrapSpec,
)

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

    def generation_counts(self, _context: TrapCaseContext) -> TrapGenerationCounts:
        return TrapGenerationCounts(generated_artifacts=1, base_cases=1, variant_cases=0)

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        {evaluate_body}
"""
    (trap_dir / "trap.py").write_text(textwrap.dedent(source), encoding="utf-8")


def _extract_manifest_path(output: str) -> Path:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("CLI output did not contain a manifest path")
    for line in reversed(lines):
        if line.startswith("Run manifest"):
            return Path(line.removeprefix("Run manifest").strip())
        if line.startswith("Report"):
            return Path(line.removeprefix("Report").strip()).parent / "run.json"
        if line.startswith("Run:"):
            return Path(line.removeprefix("Run:").strip()) / "run.json"
    return Path(lines[-1])


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
    assert len(prompts) == 6
    assert prompts[0].startswith("Scenario")
    assert prompts[1].startswith("Content style")
    assert prompts[2].startswith("Trap intent")
    assert prompts[3].startswith("Seed (optional integer)")
    assert prompts[4].startswith("What command runs your test suite?")
    assert prompts[5].startswith("Where should this command be run?")
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

    code = main(["run", "reasoning/chain-trap"])

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

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = _extract_manifest_path(captured.out)
    assert run_manifest_path.exists()
    assert "OpenTrap Run" in captured.out
    assert "Trap:      reasoning/chain-trap" in captured.out
    assert "✓ Dataset generated" in captured.out
    assert "✓ Adapter ready" in captured.out
    assert "✓ Harness completed" in captured.out
    assert "Cases" in captured.out
    assert "Case Execution" in captured.out
    assert "Trap Evaluation" in captured.out
    assert "Adapter: Host starting on" not in captured.err
    assert "Adapter: Shutdown complete" not in captured.err

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
    assert run_manifest["scorer_status"] == "completed"

    artifact_path = Path(run_manifest["traps"][0]["artifact_path"])
    assert (artifact_path / "data" / "00001.txt").read_text(encoding="utf-8") == (
        f"reasoning/chain-trap|summarize docs|7|{expected_sample_count}"
    )
    run_dir = run_manifest_path.parent
    session_id = run_manifest["sessions"][0]["session_id"]
    assert run_manifest["sessions"][0]["evidence_file"] == "traces.jsonl"
    sessions_file = run_manifest.get("sessions_file")
    assert sessions_file == "sessions.jsonl"
    sessions_path = run_dir / sessions_file
    assert sessions_path.exists()
    assert not (run_dir / f"session-{session_id}.json").exists()
    traces_path = run_dir / "traces.jsonl"
    assert traces_path.exists()
    report_path = run_dir / "report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scorer_status"] == "completed"
    assert report["counts"]["scenario_cases"] == 1
    assert report["counts"]["base_cases"] == 1
    assert report["counts"]["variant_cases"] == 0
    assert report["counts"]["selected_cases"] == 1
    assert report["counts"]["harness_executed"] == 1
    assert report["counts"]["harness_passed"] == 1
    assert report["counts"]["harness_failed"] == 0
    assert report["counts"]["scored_cases"] == 1
    assert report["counts"]["trap_successes"] == 0
    assert report["security_result"]["status"] == "no_successful_traps_detected"
    assert report["security_result"]["trap_success_count"] == 0
    assert report["security_result"]["evaluated_count"] == 1
    assert report["security_result"]["details"] == {}


def test_trap_run_surfaces_harness_output_only_when_verbose(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _base_payload()
    payload["harness"]["command"] = [
        sys.executable,
        "-c",
        "import sys; print('harness stdout'); print('harness stderr', file=sys.stderr)",
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    clean_code = main(["run", "reasoning/chain-trap"])
    clean_captured = capsys.readouterr()
    assert clean_code == 0
    assert "harness stdout" not in clean_captured.out
    assert "harness stdout" not in clean_captured.err
    assert "harness stderr" not in clean_captured.out
    assert "harness stderr" not in clean_captured.err

    verbose_code = main(["run", "reasoning/chain-trap", "--verbose"])
    verbose_captured = capsys.readouterr()
    assert verbose_code == 0
    verbose_run_manifest_path = _extract_manifest_path(verbose_captured.out)
    assert "Harness output case 1/1 (exit 0)" in verbose_captured.err
    assert "harness stdout" in verbose_captured.err
    assert "harness stderr" in verbose_captured.err
    assert "Adapter: Host starting on" in verbose_captured.err
    assert f"Run manifest  {verbose_run_manifest_path}" in verbose_captured.out
    assert f"Traces        {verbose_run_manifest_path.parent / 'traces.jsonl'}" in (
        verbose_captured.out
    )


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

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = _extract_manifest_path(captured.out)
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

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 1
    run_manifest_path = _extract_manifest_path(captured.out)
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["status"] == "finalized"
    assert run_manifest["succeeded"] is False
    sessions_file = run_manifest.get("sessions_file")
    assert sessions_file == "sessions.jsonl"
    sessions_path = run_manifest_path.parent / sessions_file
    session_payloads = [
        json.loads(line)
        for line in sessions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(session_payloads) == 1
    session_payload = session_payloads[0]
    assert session_payload["harness_exit_code"] == 3
    assert session_payload["item_id"] == "00001"
    assert "case" not in session_payload
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["harness_executed"] == 1
    assert report["counts"]["harness_passed"] == 0
    assert report["counts"]["harness_failed"] == 1
    assert report["security_result"]["status"] == "no_successful_traps_detected"
    assert report["security_result"]["evaluated_count"] == 1


def test_report_aggregates_match_sessions_jsonl_for_failed_run(
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

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 1
    run_manifest_path = _extract_manifest_path(captured.out)
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))

    sessions_file = run_manifest.get("sessions_file")
    assert sessions_file == "sessions.jsonl"
    sessions_path = run_manifest_path.parent / sessions_file
    session_payloads = [
        json.loads(line)
        for line in sessions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    computed_session_count = len(session_payloads)
    computed_failed_session_count = sum(
        1 for session in session_payloads if session.get("harness_exit_code") not in {None, 0}
    )

    assert report["run_id"] == run_manifest["run_id"]
    assert report["counts"]["harness_executed"] == computed_session_count
    assert report["counts"]["harness_failed"] == computed_failed_session_count
    assert report["counts"]["harness_passed"] == (
        computed_session_count - computed_failed_session_count
    )


def test_trap_run_marks_scorer_failed_when_evaluation_raises(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract_with_behavior(
        tmp_path / "traps",
        "reasoning/chain-trap",
        evaluate_body='raise RuntimeError("boom during evaluate")',
    )
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = _extract_manifest_path(captured.out)
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))
    assert run_manifest["status"] == "finalized"
    assert run_manifest["scorer_status"] == "failed"
    assert report["scorer_status"] == "failed"
    assert report["security_result"]["status"] == "unavailable"
    assert "Trap evaluation failed: boom during evaluate" in captured.err


@pytest.mark.parametrize(
    ("evaluate_body", "expected_status", "expected_display"),
    [
        (
            "return EvaluationResult(success_count=1, evaluated_count=2, details={'judge': 'ok'})",
            "vulnerable",
            "vulnerable",
        ),
        (
            "return EvaluationResult(success_count=0, evaluated_count=2, details=None)",
            "no_successful_traps_detected",
            "secure",
        ),
        (
            "return EvaluationResult(success_count=0, evaluated_count=0, details=None)",
            "unavailable",
            "secure",
        ),
    ],
    ids=["vulnerable", "no-successes", "unavailable"],
)
def test_trap_run_writes_security_result_and_prints_summary(
    capsys,
    tmp_path: Path,
    monkeypatch,
    evaluate_body: str,
    expected_status: str,
    expected_display: str,
) -> None:
    _write_stub_contract_with_behavior(
        tmp_path / "traps",
        "reasoning/chain-trap",
        evaluate_body=evaluate_body,
    )
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = _extract_manifest_path(captured.out)
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))
    assert report["security_result"]["status"] == expected_status
    assert "Trap Evaluation" in captured.out
    assert any(
        line.startswith("Outcome") and line.endswith(expected_display)
        for line in captured.out.splitlines()
    )
    expected_report_path = str(run_manifest_path.parent / "evaluation.csv")
    assert any(
        line.startswith("Report") and line.endswith(expected_report_path)
        for line in captured.out.splitlines()
    )

    if expected_status == "vulnerable":
        assert report["security_result"]["trap_success_count"] == 1
        assert report["security_result"]["trap_failure_count"] == 1
        assert report["security_result"]["evaluated_count"] == 2
        assert report["security_result"]["trap_success_rate"] == 0.5
        assert report["security_result"]["details"] == {"judge": "ok"}
        assert report["counts"]["trap_successes"] == 1
        assert report["counts"]["scored_cases"] == 2
        assert "Trap successes  1 / 2" in captured.out
        assert "Success rate    50.0%" in captured.out
    elif expected_status == "no_successful_traps_detected":
        assert report["security_result"]["trap_success_count"] == 0
        assert report["security_result"]["evaluated_count"] == 2
        assert report["security_result"]["trap_success_rate"] == 0.0
        assert report["security_result"]["details"] == {}
        assert report["counts"]["trap_successes"] == 0
        assert report["counts"]["scored_cases"] == 2
        assert "Trap successes  0 / 2" in captured.out
        assert "Success rate    0.0%" in captured.out
    else:
        assert report["security_result"]["evaluated_count"] == 0
        assert report["security_result"]["details"] == {}
        assert report["counts"]["trap_successes"] == 0
        assert report["counts"]["scored_cases"] == 0
        assert "⚠ Skipped  no cases were evaluated" in captured.out


def test_trap_run_marks_scorer_failed_when_evaluation_returns_legacy_mapping(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract_with_behavior(
        tmp_path / "traps",
        "reasoning/chain-trap",
        evaluate_body='return {"score": 1.0}',
    )
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code = main(["run", "reasoning/chain-trap"])

    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = _extract_manifest_path(captured.out)
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))
    assert report["scorer_status"] == "failed"
    assert report["security_result"]["status"] == "unavailable"
    assert "must return EvaluationResult" in captured.err


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

    code = main(["run", "reasoning/chain-trap"])

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

    code1 = main(["run", "reasoning/chain-trap"])
    captured1 = capsys.readouterr()
    assert code1 == 0
    run_manifest_path_1 = _extract_manifest_path(captured1.out)
    run_1 = json.loads(run_manifest_path_1.read_text(encoding="utf-8"))
    trap_1 = run_1["traps"][0]

    code2 = main(["run", "reasoning/chain-trap"])
    captured2 = capsys.readouterr()
    assert code2 == 0
    run_manifest_path_2 = _extract_manifest_path(captured2.out)
    run_2 = json.loads(run_manifest_path_2.read_text(encoding="utf-8"))
    trap_2 = run_2["traps"][0]

    assert run_1["run_id"] != run_2["run_id"]
    assert trap_1["dataset_source"] == "generated_then_cached"
    assert trap_2["dataset_source"] == "cache_hit"
    assert trap_1["dataset_fingerprint"] == trap_2["dataset_fingerprint"]
    assert trap_1["dataset_cache_dir"] == trap_2["dataset_cache_dir"]
    assert trap_1["artifact_path"] == trap_2["artifact_path"]
    assert Path(trap_1["artifact_path"]) == Path(trap_1["dataset_cache_dir"])


def test_generate_command_reports_cache_miss_then_hit_and_force_miss(
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
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code1 = main(["generate", "reasoning/chain-trap"])
    out1 = capsys.readouterr().out
    assert code1 == 0
    assert "OpenTrap Generate" in out1
    assert "Source:       cache miss" in out1
    assert not (tmp_path / "runs").exists()

    code2 = main(["generate", "reasoning/chain-trap"])
    out2 = capsys.readouterr().out
    assert code2 == 0
    assert "Source:       cache hit" in out2

    code3 = main(["generate", "reasoning/chain-trap", "--force"])
    out3 = capsys.readouterr().out
    assert code3 == 0
    assert "Source:       cache miss" in out3


def test_execute_requires_cached_dataset_then_runs_without_evaluation(
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
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    missing_code = main(["execute", "reasoning/chain-trap"])
    missing = capsys.readouterr()
    assert missing_code == 1
    assert "cached dataset is unavailable" in missing.err

    generate_code = main(["generate", "reasoning/chain-trap"])
    _ = capsys.readouterr()
    assert generate_code == 0

    execute_code = main(["execute", "reasoning/chain-trap", "--max-cases", "5"])
    captured = capsys.readouterr()
    assert execute_code == 0
    assert "OpenTrap Execute" in captured.out
    assert "Trap Evaluation" not in captured.out
    run_manifest_path = _extract_manifest_path(captured.out)
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    report = json.loads((run_manifest_path.parent / "report.json").read_text(encoding="utf-8"))
    assert run_manifest["counts"]["selected_cases"] == 1
    assert run_manifest["counts"]["harness_executed"] == 1
    assert run_manifest["scorer_status"] == "pending"
    assert report["scorer_status"] == "pending"


def test_eval_command_supports_run_id_and_latest_and_fails_nonzero_on_error(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract_with_behavior(
        tmp_path / "traps",
        "reasoning/chain-trap",
        evaluate_body='raise RuntimeError("boom during evaluate")',
    )
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    generate_code = main(["generate", "reasoning/chain-trap"])
    _ = capsys.readouterr()
    assert generate_code == 0
    execute_code = main(["execute", "reasoning/chain-trap"])
    execute_capture = capsys.readouterr()
    assert execute_code == 0
    run_manifest_path = _extract_manifest_path(execute_capture.out)
    run_id = run_manifest_path.parent.name

    eval_by_id_code = main(["eval", run_id, "--max-cases", "1"])
    eval_by_id = capsys.readouterr()
    assert eval_by_id_code == 1
    assert "OpenTrap Eval" in eval_by_id.out
    assert "Trap evaluation failed: boom during evaluate" in eval_by_id.err

    eval_latest_code = main(["eval", "latest"])
    eval_latest = capsys.readouterr()
    assert eval_latest_code == 1
    assert "OpenTrap Eval" in eval_latest.out
