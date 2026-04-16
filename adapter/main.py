from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentrap.runtime import emit_event, end_session, start_session


def _load_trap_ids(manifest_path: str) -> list[str]:
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(payload, dict):
        return []

    traps = payload.get("traps")
    if not isinstance(traps, list):
        return []

    trap_ids: list[str] = []
    for entry in traps:
        if not isinstance(entry, dict):
            continue
        trap_id = entry.get("trap_id")
        if isinstance(trap_id, str):
            trap_ids.append(trap_id)
    return trap_ids


class _AdapterServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, *, trap_ids: list[str]) -> None:
        super().__init__((host, port), _StubRequestHandler)
        self.trap_ids = trap_ids
        self.captured_events: list[dict[str, object]] = []


class _StubRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        started = time.monotonic()
        if self.path != "/__opentrap/stub":
            status = 404
            body_size = self._send_json(status, {"error": "not found"})
            self._capture_event(started, status, body_size)
            return
        trap_ids = getattr(self.server, "trap_ids", [])
        status = 200
        body_size = self._send_json(status, {"ok": True, "trap_ids": trap_ids})
        self._capture_event(started, status, body_size)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args

    def _send_json(self, status: int, payload: dict[str, object]) -> int:
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def _capture_event(self, started: float, status: int, body_size: int) -> None:
        captured = getattr(self.server, "captured_events", None)
        if not isinstance(captured, list):
            return
        captured.append(
            {
                "method": self.command,
                "path": self.path,
                "status_code": status,
                "request_size": 0,
                "response_size": body_size,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
        )


def _flush_captured_events(events: list[dict[str, object]]) -> None:
    for event in events:
        emit_event("http_exchange", event)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generated adapter entrypoint")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    trap_ids = _load_trap_ids(args.manifest)

    session_started = False
    session_finalized = False
    exit_code = 0
    server: _AdapterServer | None = None
    previous_handlers: dict[int, object] | None = None
    stop_event = threading.Event()
    try:
        start_session(args.manifest)
        session_started = True

        server = _AdapterServer(args.host, args.port, trap_ids=trap_ids)
        server.timeout = 0.5

        def _on_stop(_signal_num: int, _frame: object | None) -> None:
            del _signal_num, _frame
            stop_event.set()

        previous_handlers = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }
        signal.signal(signal.SIGTERM, _on_stop)
        signal.signal(signal.SIGINT, _on_stop)

        while not stop_event.is_set():
            server.handle_request()
    except Exception as exc:  # noqa: BLE001
        print(f"adapter host failed: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        try:
            if server is not None:
                _flush_captured_events(server.captured_events)
            if session_started and not session_finalized:
                end_session()
                session_finalized = True
        except Exception as exc:  # noqa: BLE001
            print(f"adapter shutdown failed: {exc}", file=sys.stderr)
            exit_code = 1
        if previous_handlers is not None:
            signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
            signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
        if server is not None:
            server.server_close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
