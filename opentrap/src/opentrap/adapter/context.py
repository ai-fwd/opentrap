from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException, Request

from opentrap.execution_context import ActiveSessionDescriptor

from .models import ManifestView


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
