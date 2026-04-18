# OpenTrap adapter process tests: verify module entrypoint startup, health, and clean finalization.
from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_generated_adapter(repo_root: Path, *, product: str = "default") -> None:
    generated_dir = repo_root / "adapter" / "generated" / product
    generated_dir.mkdir(parents=True, exist_ok=True)

    (generated_dir / "handlers.py").write_text(
        "from __future__ import annotations\n",
        encoding="utf-8",
    )
    (generated_dir / "routes.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from opentrap.adapter import RouteSpec


            def get_routes() -> list[RouteSpec]:
                return []
            """
        ),
        encoding="utf-8",
    )
    (generated_dir / "upstreams.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from opentrap.adapter import UpstreamSpec


            def get_upstreams() -> list[UpstreamSpec]:
                return []
            """
        ),
        encoding="utf-8",
    )


def _write_manifest(path: Path, *, repo_root: Path, product: str = "default") -> None:
    manifest = {
        "run_id": "test-run-id",
        "repo_root": str(repo_root),
        "product_under_test": product,
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


def _wait_for_health(
    port: int,
    process: subprocess.Popen[str],
    timeout_seconds: float = 5.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/__opentrap/health"
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
    raise AssertionError(f"health route never became ready: {last_error}")


def test_adapter_host_starts_serves_health_and_stays_alive(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_generated_adapter(repo_root)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opentrap.adapter",
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
        cwd=_repo_root(),
    )

    try:
        payload = _wait_for_health(port, process)
        assert payload["ok"] is True
        assert payload["trap_ids"] == ["reasoning/chain-trap"]
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_adapter_host_finalizes_run_artifacts_on_shutdown(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_generated_adapter(repo_root)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    port = _find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opentrap.adapter",
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
        cwd=_repo_root(),
    )

    try:
        _wait_for_health(port, process)
        with urlopen(f"http://127.0.0.1:{port}/__opentrap/health", timeout=0.2) as response:  # noqa: S310
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
