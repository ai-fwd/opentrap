# OpenTrap real-trap CLI integration tests.
# Verifies cache/orchestration with deterministic mocked OpenAI responses.
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

import pytest
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
        "harness": {
            "command": [sys.executable, "-c", "pass"],
            "cwd": ".",
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


def _configure_cli_paths(
    *,
    monkeypatch,
    tmp_path: Path,
    config_path: Path,
    samples_dir: Path,
    generated_root: Path,
) -> None:
    """Point CLI path defaults to isolated test directories."""
    monkeypatch.setattr("opentrap.cli.DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_TRAPS_DIR", _traps_root())
    monkeypatch.setattr("opentrap.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("opentrap.cli.DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("opentrap.cli.DEFAULT_SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("opentrap.cli.DEFAULT_DATASET_DIR", tmp_path / ".opentrap" / "dataset")
    monkeypatch.setattr("opentrap.cli.DEFAULT_ADAPTER_GENERATED_ROOT", generated_root)


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


def _install_empty_dotenv(monkeypatch) -> None:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.dotenv_values = lambda _path: {}
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)


def _read_trap_entry(run_manifest_path: Path) -> dict:
    """Load first trap entry from a run manifest file."""
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    return run_manifest["traps"][0]


def _prepare_llm_trap_run(
    *,
    monkeypatch,
    tmp_path: Path,
    generated_root: Path,
    payload: dict,
) -> tuple[Path, Path]:
    """Prepare config, env, and deterministic fake openai for integration run."""
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_URL", "https://api.openai.com")
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
        generated_root=generated_root,
    )
    # Avoid fixed-port collisions across tests without opening probe sockets.
    # The test temp path is unique per test case/process, so this produces a
    # stable high port that is very unlikely to clash in normal CI usage.
    port_seed = abs(hash(str(tmp_path))) % 20000
    monkeypatch.setattr("opentrap.run_orchestration.ADAPTER_PORT", 20000 + port_seed)
    return config_path, samples_dir


def test_llm_mocked_run_reuses_dataset_when_inputs_are_unchanged(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure cache hit behavior for identical trap inputs across repeated runs."""
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
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


def test_llm_selected_trap_fails_fast_when_llm_env_is_missing(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    _install_empty_dotenv(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_URL", raising=False)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _configure_cli_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=samples_dir,
        generated_root=generated_root,
    )

    code = main([TRAP_ID])

    captured = capsys.readouterr()
    assert code == 1
    assert (
        "Missing required environment variable(s): OPENAI_API_KEY, OPENAI_URL, OPENAI_MODEL"
        in captured.err
    )


def test_llm_mocked_run_uses_final_cache_paths_for_manifest_data_items(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ensure manifest data item paths point at the finalized cache artifact, not staging."""
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
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
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    config_path, _samples_dir = _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
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
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    _config_path, samples_dir = _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
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


def test_llm_mocked_run_writes_trap_local_evaluation_artifacts(
    capsys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    generated_root = tmp_path / "adapter" / "generated"
    _write_generated_adapter(generated_root)
    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
        payload=_base_payload(base_count=1),
    )

    code = main([TRAP_ID])
    captured = capsys.readouterr()
    assert code == 0
    run_manifest_path = Path(captured.out.strip())
    run_dir = run_manifest_path.parent

    evaluation_jsonl = run_dir / "evaluation.jsonl"
    evaluation_csv = run_dir / "evaluation.csv"
    evaluation_summary = run_dir / "evaluation_summary.json"

    assert evaluation_jsonl.exists()
    assert evaluation_csv.exists()
    assert evaluation_summary.exists()

    summary_payload = json.loads(evaluation_summary.read_text(encoding="utf-8"))
    assert summary_payload["total_cases"] == 8
    assert "average_rouge_l_f1" in summary_payload
    assert "min_rouge_l_f1" in summary_payload
    assert "max_rouge_l_f1" in summary_payload
    assert "average_sbert_cosine_similarity" in summary_payload
    assert "min_sbert_cosine_similarity" in summary_payload
    assert "max_sbert_cosine_similarity" in summary_payload
    assert "grouped_averages_by_injection_type" in summary_payload
    assert "grouped_success_rate_by_injection_type" in summary_payload

    jsonl_rows = [
        json.loads(line)
        for line in evaluation_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(jsonl_rows) == 8
    assert all("category" not in row for row in jsonl_rows)
    assert all("rouge_l_f1" in row for row in jsonl_rows)
    assert all("sbert_cosine_similarity" in row for row in jsonl_rows)


@pytest.mark.parametrize(
    ("handlers_prelude", "use_missing_generated_root", "set_ready_timeout", "expected_error"),
    [
        ("", True, False, "generated adapter output was not found"),
        (
            "raise RuntimeError('boom before startup')",
            False,
            False,
            "adapter exited before health check succeeded",
        ),
        ("import time\ntime.sleep(1.0)", False, True, "timed out waiting for adapter health"),
    ],
    ids=["missing-generated-output", "adapter-import-exit", "adapter-health-timeout"],
)
def test_trap_run_surfaces_adapter_startup_failures(
    capsys,
    tmp_path: Path,
    monkeypatch,
    handlers_prelude: str,
    use_missing_generated_root: bool,
    set_ready_timeout: bool,
    expected_error: str,
) -> None:
    """Surface distinct adapter startup failures with explicit troubleshooting messages."""
    if use_missing_generated_root:
        generated_root = tmp_path / "missing-generated"
    else:
        generated_root = tmp_path / "adapter" / "generated"
        _write_generated_adapter(generated_root, handlers_prelude=handlers_prelude)

    _prepare_llm_trap_run(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        generated_root=generated_root,
        payload=_base_payload(),
    )
    if set_ready_timeout:
        monkeypatch.setattr("opentrap.run_orchestration.ADAPTER_READY_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr("opentrap.run_orchestration.ADAPTER_POLL_INTERVAL_SECONDS", 0.01)

    code = main([TRAP_ID])

    captured = capsys.readouterr()
    assert code == 1
    assert expected_error in captured.err
