from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .gen_loader import load_generated_adapter

STATUS_PREFIX = "[adapter]"


def _status(message: str) -> None:
    print(f"{STATUS_PREFIX} {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenTrap adapter runtime")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    loaded = load_generated_adapter(manifest_path)

    app = create_app(
        manifest_path=manifest_path,
        routes=loaded.routes,
        upstreams=loaded.upstreams,
    )

    _status(
        "Host starting on "
        f"{args.host}:{args.port} for adapter product '{loaded.product}' "
        f"from {loaded.generated_dir}; waiting for signal"
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
        _status("Signal received; flushing and finalizing")
        server.should_exit = True

    previous_handlers = {
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)

    exit_code = 0
    try:
        server.run()
    except Exception as exc:  # noqa: BLE001
        _status(f"Shutdown failure: adapter host failed: {exc}")
        exit_code = 1
    finally:
        signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
        signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
        _status("Shutdown complete")

    return exit_code
