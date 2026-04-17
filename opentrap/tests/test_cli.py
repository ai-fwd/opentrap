from __future__ import annotations

import json
import subprocess
import textwrap
import threading
import time
from pathlib import Path

import yaml

import opentrap.cli as cli_module
from opentrap.cli import main


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _write_stub_adapter_sleep(path: Path, *, seconds: float) -> None:
    source = f"""
from __future__ import annotations

import argparse
import time

from opentrap.runtime import start_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    start_session(args.manifest)
    time.sleep({seconds})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _write_fastapi_smoke_adapter(path: Path, *, seconds: float) -> None:
    source = f"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, {str(_repo_root())!r})
from adapter.host import create_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    app = create_app(manifest_path=Path(args.manifest), routes=[], upstreams=[])
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def _stop_later() -> None:
        time.sleep({seconds})
        server.should_exit = True

    def _on_stop(_signal_num: int, _frame: object | None) -> None:
        del _signal_num, _frame
        server.should_exit = True

    previous_handlers = {{
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }}
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)
    threading.Thread(target=_stop_later, daemon=True).start()

    try:
        server.run()
    finally:
        signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
        signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
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


def test_trap_run_stays_attached_while_adapter_is_alive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_stub_adapter_sleep(adapter_path, seconds=0.8)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")

    _configure_trap_run_paths(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        config_path=config_path,
        samples_dir=tmp_path / ".opentrap" / "samples",
        adapter_path=adapter_path,
    )

    result: dict[str, int] = {}

    def _run() -> None:
        result["code"] = main(["reasoning/chain-trap"])

    thread = threading.Thread(target=_run)
    thread.start()
    time.sleep(0.2)
    assert thread.is_alive()

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result["code"] == 0


def test_trap_run_with_fastapi_adapter_smoke(capsys, tmp_path: Path, monkeypatch) -> None:
    _write_stub_contract(tmp_path / "traps", "reasoning/chain-trap")
    adapter_path = tmp_path / "adapter-main.py"
    _write_fastapi_smoke_adapter(adapter_path, seconds=0.8)

    config_path = tmp_path / ".opentrap" / "opentrap.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(_base_payload(), sort_keys=False), encoding="utf-8")

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
    assert run_manifest["status"] == "finalized"
    assert run_manifest["active_session_id"] is None
    assert isinstance(run_manifest.get("report_path"), str)
    report_path = Path(run_manifest["report_path"])
    assert report_path.exists()

    sessions = run_manifest["sessions"]
    assert isinstance(sessions, list)
    assert len(sessions) == 1
    assert sessions[0]["ended_at_utc"] is not None


def test_wait_for_adapter_exit_terminates_process_on_interrupt(monkeypatch) -> None:
    class _Process:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    process = _Process()

    def _sleep_then_interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("opentrap.cli.time.sleep", _sleep_then_interrupt)
    cli_module._wait_for_adapter_exit(process)  # type: ignore[arg-type]
    assert process.terminated


def test_wait_for_adapter_exit_kills_process_when_terminate_wait_times_out(monkeypatch) -> None:
    class _Process:
        def __init__(self) -> None:
            self.terminated = False
            self.killed = False
            self.wait_calls = 0

        def poll(self) -> None | int:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if timeout is not None:
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired(cmd="adapter", timeout=timeout)
                return 0
            return 0

        def kill(self) -> None:
            self.killed = True

    process = _Process()

    def _sleep_then_interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("opentrap.cli.time.sleep", _sleep_then_interrupt)
    cli_module._wait_for_adapter_exit(process)  # type: ignore[arg-type]
    assert process.terminated
    assert process.killed


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
    assert Path(trap_1["artifact_path"]).parent == Path(trap_1["dataset_cache_dir"])


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
