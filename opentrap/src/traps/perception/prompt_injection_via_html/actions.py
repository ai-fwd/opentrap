from __future__ import annotations

from pathlib import Path

from opentrap.execution_context import get_current_execution_context


class TrapActions:
    def __init__(self, *, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir

    def get_current_data(self) -> str:
        descriptor = get_current_execution_context()
        case = descriptor.case
        data_item = case.get("data_item")
        if not isinstance(data_item, dict):
            raise RuntimeError("active case is missing data_item metadata")
        path_value = data_item.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise RuntimeError("active case data_item path is unavailable")
        return Path(path_value).read_text(encoding="utf-8")
