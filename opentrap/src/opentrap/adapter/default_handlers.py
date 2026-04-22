from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import Response

from .context import RequestContext


async def observe_openai_responses_default(
    ctx: RequestContext,
    snapshot: Response,
) -> Mapping[str, Any]:
    payload: dict[str, Any] = {"model": None, "output": None}
    try:
        raw_body = await ctx.request.body()
        request_json = json.loads(raw_body.decode("utf-8", errors="replace"))
        if isinstance(request_json, dict):
            model = request_json.get("model")
            if isinstance(model, str):
                payload["model"] = model
    except Exception:  # noqa: BLE001
        pass

    try:
        response_json = json.loads((snapshot.body or b"").decode("utf-8", errors="replace"))
        if isinstance(response_json, dict):
            payload["output"] = response_json.get("output")
    except Exception:  # noqa: BLE001
        pass

    return payload
