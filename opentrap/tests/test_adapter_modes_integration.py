from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


class _UpstreamServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), _UpstreamHandler)
        self.requests: list[dict[str, str]] = []


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/up/"):
            self._send_json(404, {"error": "not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        self._capture(body)

        item_id = self.path.split("/up/", 1)[1].split("?", 1)[0]
        payload = {
            "ok": True,
            "item_id": item_id,
            "body": body,
        }
        self._send_json(201, payload, headers={"x-upstream-mode": "passthrough"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/observe"):
            self._capture("")
            body = b"observe-from-upstream\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("x-upstream-mode", "observe")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._send_json(404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args

    def _capture(self, body: str) -> None:
        server = self.server
        if not isinstance(server, _UpstreamServer):
            return
        server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "body": body,
            }
        )

    def _send_json(
        self,
        status: int,
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if headers is not None:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_manifest(path: Path) -> None:
    manifest = {
        "run_id": "integration-run-id",
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


def _write_temp_adapter(path: Path, *, upstream_base_url: str, observer_marker: Path) -> None:
    source = f"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse

sys.path.insert(0, {str(_repo_root())!r})

from adapter.host import RequestContext, RouteSpec, UpstreamSpec, create_app
from http import HTTPMethod


async def intercept_handler(ctx: RequestContext):
    payload = {{
        "route": "intercept",
        "session_id": ctx.session_id,
    }}
    return JSONResponse(payload)


async def observe_handler(_ctx: RequestContext, snapshot) -> None:
    marker = Path({str(observer_marker)!r})
    marker.write_text(
        json.dumps({{"status_code": snapshot.status_code}}) + "\\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    routes = [
        RouteSpec(
            name="intercept",
            path="/intercept",
            methods=(HTTPMethod.GET,),
            mode="intercept",
            handler=intercept_handler,
        ),
        RouteSpec(
            name="passthrough",
            path="/passthrough/{{id}}",
            methods=(HTTPMethod.POST,),
            mode="passthrough",
            upstream="origin",
            upstream_path="/up/{{id}}",
        ),
        RouteSpec(
            name="observe",
            path="/observe",
            methods=(HTTPMethod.GET,),
            mode="observe",
            upstream="origin",
            handler=observe_handler,
        ),
    ]
    upstreams = [
        UpstreamSpec(
            name="origin",
            base_url={upstream_base_url!r},
        )
    ]

    app = create_app(
        manifest_path=Path(args.manifest),
        routes=routes,
        upstreams=upstreams,
    )

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def _on_stop(_signal_num: int, _frame: object | None) -> None:
        del _signal_num, _frame
        server.should_exit = True

    previous_handlers = {{
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }}
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)
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
                return json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"health route never became ready: {last_error}")


@contextmanager
def _run_upstream_server(host: str, port: int) -> Iterator[_UpstreamServer]:
    server = _UpstreamServer(host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_adapter_process_integrates_route_modes_and_named_upstreams(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / "run.json"
    _write_manifest(manifest_path)

    upstream_port = _find_free_port()
    adapter_port = _find_free_port()
    observer_marker = run_dir / "observe-marker.json"

    adapter_path = tmp_path / "adapter-modes.py"
    _write_temp_adapter(
        adapter_path,
        upstream_base_url=f"http://127.0.0.1:{upstream_port}",
        observer_marker=observer_marker,
    )

    with _run_upstream_server("127.0.0.1", upstream_port) as upstream_server:
        process = subprocess.Popen(
            [
                sys.executable,
                str(adapter_path),
                "--manifest",
                str(manifest_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(adapter_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            health = _wait_for_health(adapter_port, process)
            assert health["ok"] is True
            assert health["trap_ids"] == ["reasoning/chain-trap"]

            with urlopen(f"http://127.0.0.1:{adapter_port}/intercept", timeout=0.5) as response:  # noqa: S310
                assert response.status == 200
                payload = json.loads(response.read().decode("utf-8"))
                assert payload["route"] == "intercept"
                assert payload["session_id"]

            passthrough_request = Request(
                f"http://127.0.0.1:{adapter_port}/passthrough/abc?x=1",
                data=b"hello",
                method="POST",
            )
            with urlopen(passthrough_request, timeout=0.5) as response:  # noqa: S310
                assert response.status == 201
                assert response.headers["x-upstream-mode"] == "passthrough"
                payload = json.loads(response.read().decode("utf-8"))
                assert payload == {
                    "ok": True,
                    "item_id": "abc",
                    "body": "hello",
                }

            with urlopen(f"http://127.0.0.1:{adapter_port}/observe", timeout=0.5) as response:  # noqa: S310
                assert response.status == 200
                assert response.headers["x-upstream-mode"] == "observe"
                assert response.read().decode("utf-8") == "observe-from-upstream\n"

            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert run_manifest["status"] == "finalized"
    assert run_manifest["active_session_id"] is None
    assert isinstance(run_manifest.get("report_path"), str)
    report_path = Path(run_manifest["report_path"])
    assert report_path.exists()

    sessions = run_manifest["sessions"]
    assert isinstance(sessions, list)
    assert len(sessions) == 1
    session_id = sessions[0]["session_id"]
    evidence_path = run_dir / f"session-{session_id}.jsonl"

    envelopes = [
        json.loads(line)
        for line in evidence_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert envelopes
    exchanges = [event for event in envelopes if event["event_type"] == "http_exchange"]
    assert exchanges

    paths = [entry["payload"]["path"] for entry in exchanges]
    assert "/__opentrap/health" in paths
    assert "/intercept" in paths
    assert "/passthrough/abc" in paths
    assert "/observe" in paths

    captured = upstream_server.requests
    assert len(captured) == 2
    assert captured[0] == {
        "method": "POST",
        "path": "/up/abc?x=1",
        "body": "hello",
    }
    assert captured[1] == {
        "method": "GET",
        "path": "/observe",
        "body": "",
    }

    assert observer_marker.exists()
    observer_payload = json.loads(observer_marker.read_text(encoding="utf-8"))
    assert observer_payload["status_code"] == 200
