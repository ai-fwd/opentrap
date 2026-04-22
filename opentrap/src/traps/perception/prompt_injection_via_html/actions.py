from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class TrapActions:
    def __init__(self, *, data_dir: Path) -> None:
        self._data_dir = data_dir

    def get_data_for_selector(self, selector: Any) -> str:
        items = self._sorted_items()
        if not items:
            raise RuntimeError("no trap data items are available")
        digest = hashlib.sha256(str(selector).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % len(items)
        return items[index].read_text(encoding="utf-8")

    def _sorted_items(self) -> tuple[Path, ...]:
        return tuple(
            sorted(
                (
                    path
                    for path in self._data_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in {".htm", ".html"}
                ),
                key=lambda path: path.name,
            )
        )
