from __future__ import annotations

import argparse

from opentrap.runtime import start_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generated adapter entrypoint")
    parser.add_argument("--manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    start_session(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
