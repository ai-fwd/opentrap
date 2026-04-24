from __future__ import annotations

import inspect
import time
from collections.abc import Iterable, Mapping
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request, Response

from opentrap.execution_context import ActiveSessionDescriptor, emit_event

from .context import RequestContext
from .models import InterceptHandler, ManifestView, ObserveHandler, RouteSpec, UpstreamSpec


def build_upstream_map(upstreams: Iterable[UpstreamSpec]) -> dict[str, UpstreamSpec]:
    upstream_map: dict[str, UpstreamSpec] = {}
    for upstream in upstreams:
        if not upstream.name.strip():
            raise ValueError("upstream name cannot be empty")
        if not upstream.base_url.strip():
            raise ValueError(f"upstream '{upstream.name}' base_url cannot be empty")
        if upstream.name in upstream_map:
            raise ValueError(f"duplicate upstream name: {upstream.name}")
        upstream_map[upstream.name] = upstream
    return upstream_map


def validate_handler_signature(route: RouteSpec) -> None:
    if route.mode == "intercept":
        handler = route.handler
        if handler is None:
            raise ValueError(f"route '{route.name}' requires a handler")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"route '{route.name}' handler must be async")
        if len(inspect.signature(handler).parameters) != 1:
            raise ValueError(
                f"route '{route.name}' intercept handler must accept exactly one argument"
            )
        return

    if route.mode == "observe":
        handler = route.handler
        if handler is None:
            return
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"route '{route.name}' observe handler must be async")
        if len(inspect.signature(handler).parameters) != 2:
            raise ValueError(
                f"route '{route.name}' observe handler must accept exactly two arguments"
            )


def validate_route_specs(
    routes: Iterable[RouteSpec],
    upstream_map: Mapping[str, UpstreamSpec],
) -> None:
    for route in routes:
        if not route.name.strip():
            raise ValueError("route name cannot be empty")
        if not route.path.startswith("/"):
            raise ValueError(f"route '{route.name}' path must start with '/'")
        if not route.methods:
            raise ValueError(f"route '{route.name}' must define at least one method")

        if route.mode == "intercept":
            if route.upstream is not None:
                raise ValueError(f"route '{route.name}' must not define an upstream")
            if route.handler is None:
                raise ValueError(f"route '{route.name}' requires a handler")
        elif route.mode == "passthrough":
            if route.handler is not None:
                raise ValueError(f"route '{route.name}' must not define a handler")
            if route.upstream is None:
                raise ValueError(f"route '{route.name}' requires an upstream")
        elif route.mode == "observe":
            if route.upstream is None:
                raise ValueError(f"route '{route.name}' requires an upstream")
        else:
            raise ValueError(f"route '{route.name}' has unsupported mode: {route.mode}")

        if route.upstream is not None and route.upstream not in upstream_map:
            raise ValueError(f"route '{route.name}' has unknown upstream '{route.upstream}'")

        validate_handler_signature(route)


async def _request_body(request: Request) -> bytes:
    if hasattr(request.state, "raw_body"):
        return cast(bytes, request.state.raw_body)
    raw_body = await request.body()
    request.state.raw_body = raw_body
    return raw_body


def copy_request_with_body(request: Request, body: bytes) -> Request:
    consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    updated = Request(request.scope, receive)
    updated.state.raw_body = body
    request_id = getattr(request.state, "request_id", None)
    if request_id is not None:
        updated.state.request_id = request_id
    execution_context = getattr(request.state, "execution_context", None)
    if execution_context is not None:
        updated.state.execution_context = execution_context
    return updated


def _target_url(base_url: str, route: RouteSpec, request: Request) -> str:
    path_template = route.upstream_path if route.upstream_path is not None else request.url.path
    try:
        resolved_path = path_template.format(**request.path_params)
    except KeyError as exc:
        raise RuntimeError(
            f"route '{route.name}' upstream_path references unknown path param: {exc.args[0]}"
        ) from exc

    if not resolved_path.startswith("/"):
        resolved_path = f"/{resolved_path}"

    base = base_url.rstrip("/")
    query = request.url.query
    if query:
        return f"{base}{resolved_path}?{query}"
    return f"{base}{resolved_path}"


def _filtered_request_headers(request: Request) -> dict[str, str]:
    blocked = {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    return {name: value for name, value in request.headers.items() if name.lower() not in blocked}


def _filtered_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    blocked = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    return {name: value for name, value in headers.items() if name.lower() not in blocked}


async def _forward_request(*, app: FastAPI, request: Request, route: RouteSpec) -> Response:
    upstream_name = route.upstream
    if upstream_name is None:
        raise RuntimeError(f"route '{route.name}' missing upstream")

    upstream_map = cast(dict[str, UpstreamSpec], app.state.upstream_map)
    upstream = upstream_map[upstream_name]
    target_url = _target_url(upstream.base_url, route, request)
    raw_body = await _request_body(request)

    client = cast(httpx.AsyncClient, app.state.forward_client)
    forwarded = await client.request(
        method=request.method,
        url=target_url,
        content=raw_body,
        headers=_filtered_request_headers(request),
        timeout=None,
    )

    return Response(
        content=forwarded.content,
        status_code=forwarded.status_code,
        headers=_filtered_response_headers(dict(forwarded.headers)),
    )


async def dispatch_route(*, app: FastAPI, request: Request, route: RouteSpec) -> Response:
    request_body = await _request_body(request)
    execution_context = cast(ActiveSessionDescriptor, request.state.execution_context)
    context = RequestContext(
        request=request,
        run_id=cast(str, app.state.run_id),
        session_id=execution_context.session_id,
        request_id=cast(str, request.state.request_id),
        manifest=cast(ManifestView, app.state.manifest),
        execution_context=execution_context,
        trap_actions=cast(object | None, app.state.trap_actions),
    )
    event_common_payload: dict[str, Any] = {
        "request_id": context.request_id,
        "route_name": route.name,
        "route_mode": route.mode,
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
    }
    emit_event(
        execution_context=execution_context,
        event_type="route_dispatch_pre",
        payload={
            **event_common_payload,
            "request_headers": dict(request.headers),
            "request_body": request_body.decode("utf-8", errors="replace"),
        },
    )
    started = time.monotonic()
    post_emitted = False

    response: Response | None = None
    try:
        if route.mode == "intercept":
            intercept_handler = cast(InterceptHandler, route.handler)
            response = await intercept_handler(context)
            return response

        forwarded_response = await _forward_request(app=app, request=request, route=route)
        response = forwarded_response

        if route.mode == "passthrough":
            return forwarded_response

        observer = cast(ObserveHandler | None, route.handler)
        if observer is not None:
            observer_snapshot = Response(
                content=forwarded_response.body,
                status_code=forwarded_response.status_code,
                headers=dict(forwarded_response.headers),
            )
            observer_payload = await observer(context, observer_snapshot)
            if observer_payload is not None:
                emit_event(
                    execution_context=execution_context,
                    event_type="llm_responses_observed",
                    payload={
                        **event_common_payload,
                        **dict(observer_payload),
                    },
                )

        return forwarded_response
    except Exception as exc:
        emit_event(
            execution_context=execution_context,
            event_type="route_dispatch_post",
            payload={
                **event_common_payload,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "error": str(exc),
            },
        )
        post_emitted = True
        raise
    finally:
        if response is not None and not post_emitted:
            raw_response = getattr(response, "body", b"")
            if isinstance(raw_response, str):
                raw_response_bytes = raw_response.encode("utf-8", errors="replace")
            elif isinstance(raw_response, bytes):
                raw_response_bytes = raw_response
            elif isinstance(raw_response, bytearray):
                raw_response_bytes = bytes(raw_response)
            else:
                raw_response_bytes = str(raw_response).encode("utf-8", errors="replace")
            emit_event(
                execution_context=execution_context,
                event_type="route_dispatch_post",
                payload={
                    **event_common_payload,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "status_code": response.status_code,
                    "response_headers": dict(response.headers),
                    "response_body": raw_response_bytes.decode("utf-8", errors="replace"),
                },
            )
