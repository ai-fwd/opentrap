from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _adapter_main_path() -> Path:
    return Path(__file__).resolve().parents[2] / "adapter" / "main.py"


def _write_manifest(path: Path) -> None:
    manifest = {
        "run_id": "test-run-id",
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
                    {"id": "00001", "path": "dataset/item-00001.txt"},
                ],
            }
        ],
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _read_run_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_for_stub(port: int, process: subprocess.Popen[str], timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/__opentrap/stub"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"adapter exited early with code {process.returncode}: {stderr}")
        try:
            with urlopen(url, timeout=0.2) as response:  # noqa: S310
                assert response.status == 200
                payload = json.loads(response.read().decode("utf-8"))
                return payload
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"stub route never became ready: {last_error}")


def test_adapter_host_starts_serves_stub_and_stays_alive(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(_adapter_main_path()),
            "--manifest",
            str(manifest_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        payload = _wait_for_stub(port, process)
        assert payload["ok"] is True
        assert payload["trap_ids"] == ["reasoning/chain-trap"]
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_adapter_host_exits_cleanly_on_sigterm(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(_adapter_main_path()),
            "--manifest",
            str(manifest_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_stub(port, process)
        process.send_signal(signal.SIGTERM)
        exit_code = process.wait(timeout=5)
        assert exit_code == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_adapter_host_flushes_captured_events_on_shutdown(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(_adapter_main_path()),
            "--manifest",
            str(manifest_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_stub(port, process)
        with urlopen(f"http://127.0.0.1:{port}/__opentrap/stub", timeout=0.2) as response:  # noqa: S310
            assert response.status == 200

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0

        run_manifest = _read_run_manifest(manifest_path)
        session_id = run_manifest["sessions"][0]["session_id"]
        evidence_path = run_dir / f"session-{session_id}.jsonl"
        lines = [
            line.strip()
            for line in evidence_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_adapter_host_finalizes_run_artifacts_on_shutdown(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(_adapter_main_path()),
            "--manifest",
            str(manifest_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_stub(port, process)
        with urlopen(f"http://127.0.0.1:{port}/__opentrap/stub", timeout=0.2) as response:  # noqa: S310
            assert response.status == 200

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0

        run_manifest = _read_run_manifest(manifest_path)
        assert run_manifest["status"] == "finalized"
        assert run_manifest["active_session_id"] is None
        assert isinstance(run_manifest.get("report_path"), str)
        report_path = Path(run_manifest["report_path"])
        assert report_path.exists()

        sessions = run_manifest["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) == 1
        assert sessions[0]["ended_at_utc"] is not None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
