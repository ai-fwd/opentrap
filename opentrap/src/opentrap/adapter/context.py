from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException, Request

from opentrap.execution_context import ActiveSessionDescriptor

from .models import DataItemView, ManifestView


class RuntimeProtocol(Protocol):
    def start_session(self, manifest_path: str | Path) -> str: ...

    def end_session(self) -> object: ...

    def emit_event(self, event_type: str, payload: Mapping[str, Any]) -> None: ...

    def list_data_items(self) -> list[object]: ...

    def get_data_item(self, item_id: str) -> object: ...


class _DefaultRuntime:
    from opentrap.runtime import (
        emit_event,
        end_session,
        get_data_item,
        list_data_items,
        start_session,
    )


class DataItems:
    def __init__(self, *, runtime: RuntimeProtocol, base_dir: Path) -> None:
        self._runtime = runtime
        self._base_dir = base_dir

    def list(self) -> tuple[DataItemView, ...]:
        return tuple(self._coerce_item(item) for item in self._runtime.list_data_items())

    def list_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.list())

    def get(self, item_id: str) -> DataItemView:
        return self._coerce_item(self._runtime.get_data_item(item_id), expected_id=item_id)

    def read_text(self, item_id: str, *, encoding: str = "utf-8") -> str:
        return self.get(item_id).path.read_text(encoding=encoding)

    def read_bytes(self, item_id: str) -> bytes:
        return self.get(item_id).path.read_bytes()

    def _coerce_item(self, item: object, *, expected_id: str | None = None) -> DataItemView:
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str) or not item_id:
            raise RuntimeError("data item has invalid id")
        if expected_id is not None and item_id != expected_id:
            raise RuntimeError(
                f"data item lookup mismatch: expected '{expected_id}', got '{item_id}'"
            )
        relative_path = getattr(item, "path", None)
        if not isinstance(relative_path, str) or not relative_path:
            raise RuntimeError(f"data item '{item_id}' has invalid path")
        path = Path(relative_path)
        if path.is_absolute():
            return DataItemView(id=item_id, path=path)
        return DataItemView(id=item_id, path=self._base_dir / path)


@dataclass(frozen=True)
class RequestContext:
    request: Request
    run_id: str
    session_id: str
    request_id: str
    manifest: ManifestView
    execution_context: ActiveSessionDescriptor
    trap_actions: object | None

    def path_param(self, name: str, *, required: bool = True) -> str | None:
        value = self.request.path_params.get(name)
        if value is None:
            if required:
                raise HTTPException(status_code=400, detail=f"Missing path parameter: {name}")
            return None

        if isinstance(value, str):
            return value

        return str(value)

    async def json_body(self) -> object:
        try:
            return await self.request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc

    async def body_bytes(self) -> bytes:
        return await self.request.body()

    async def body_text(self, *, encoding: str = "utf-8") -> str:
        return (await self.body_bytes()).decode(encoding=encoding, errors="replace")
