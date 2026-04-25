from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import Response

from opentrap.execution_context import emit_observation

from .context import RequestContext


def _extract_output_text_and_content_type(response_json: object) -> tuple[str, str]:
    if not isinstance(response_json, dict):
        return ("", "application/json")

    output = response_json.get("output")
    if not isinstance(output, list):
        return ("", "application/json")

    text_parts: list[str] = []
    content_type = "application/json"

    for output_item in output:
        if not isinstance(output_item, dict):
            continue

        if output_item.get("type") == "output_text":
            direct_text = output_item.get("text")
            if isinstance(direct_text, str):
                text_parts.append(direct_text)
                content_type = "text/plain"

        content = output_item.get("content")
        if not isinstance(content, list):
            continue

        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") != "output_text":
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
                content_type = "text/plain"

    return ("".join(text_parts), content_type)


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
        content, content_type = _extract_output_text_and_content_type(response_json)
        if content:
            emit_observation(
                execution_context=ctx.execution_context,
                request_id=ctx.request_id,
                observation_type="llm.response",
                content_type=content_type,
                content=content,
                model=payload["model"] if isinstance(payload["model"], str) else None,
                status_code=snapshot.status_code,
            )
    except Exception:  # noqa: BLE001
        pass

    return payload
