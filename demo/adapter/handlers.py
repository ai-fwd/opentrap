from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response
from fastapi.responses import JSONResponse

import opentrap.adapter.default_handlers as handlers
from opentrap.adapter import RequestContext

_UPSTREAM_HEADERS = {"Cache-Control": "no-store"}


async def intercept_upstream_email(ctx: RequestContext) -> Response:
    email_id = _read_email_id(ctx)
    if email_id is None:
        return _upstream_error(ctx.request_id, 400, "Email id is required")

    if ctx.trap_actions is None:
        return _upstream_error(ctx.request_id, 404, "No trap data items are available")

    try:
        raw_html = ctx.trap_actions.get_current_data()
    except RuntimeError:
        return _upstream_error(
            ctx.request_id,
            404,
            f"Trap data for email id '{email_id}' is unavailable",
        )

    return _upstream_json(ctx.request_id, {"id": email_id, "rawHtml": raw_html})


async def observe_openai_responses(ctx: RequestContext, snapshot: Response):
    return await handlers.observe_openai_responses_default(ctx, snapshot)


def _read_email_id(ctx: RequestContext) -> str | None:
    value = ctx.request.path_params.get("email_id")
    if not isinstance(value, str):
        return None
    email_id = value.strip()
    return email_id if email_id else None


def _upstream_json(request_id: str, payload: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={**_UPSTREAM_HEADERS, "x-request-id": request_id},
    )


def _upstream_error(request_id: str, status_code: int, error_message: str) -> JSONResponse:
    timestamp = datetime.now(tz=UTC).isoformat()
    return _upstream_json(request_id, {"error": error_message, "timestamp": timestamp}, status_code)
