from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .context import RuntimeProtocol, _DefaultRuntime
from .http_runtime import (
    build_upstream_map,
    copy_request_with_body,
    dispatch_route,
    validate_route_specs,
)
from .manifest import load_manifest_metadata
from .models import RouteSpec, UpstreamSpec
from .trap_binding import resolve_trap_actions


def create_app(
    *,
    manifest_path: Path,
    routes: list[RouteSpec],
    upstreams: list[UpstreamSpec],
    runtime: RuntimeProtocol | None = None,
    forward_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    runtime_impl: RuntimeProtocol = (
        runtime if runtime is not None else cast(RuntimeProtocol, _DefaultRuntime)
    )
    metadata = load_manifest_metadata(manifest_path)
    upstream_map = build_upstream_map(upstreams)
    validate_route_specs(routes, upstream_map)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.manifest_path = manifest_path
        app.state.run_dir = manifest_path.parent
        app.state.manifest = metadata.manifest
        app.state.repo_root = metadata.manifest.repo_root
        app.state.run_id = metadata.run_id
        app.state.trap_ids = metadata.trap_ids
        app.state.runtime = runtime_impl
        app.state.event_emitter = runtime_impl.emit_event
        app.state.upstream_map = upstream_map
        app.state.trap_actions = resolve_trap_actions(metadata.manifest)

        if forward_client is None:
            app.state.forward_client = httpx.AsyncClient(follow_redirects=False)
        else:
            app.state.forward_client = forward_client

        session_started = False
        try:
            session_id = runtime_impl.start_session(str(manifest_path))
            app.state.session_id = session_id
            session_started = True
            yield
        finally:
            client = cast(httpx.AsyncClient, app.state.forward_client)
            await client.aclose()
            if session_started:
                runtime_impl.end_session()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def evidence_middleware(request: Request, call_next):
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        raw_body = await request.body()
        request_with_body = copy_request_with_body(request, raw_body)
        return await call_next(request_with_body)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = cast(str | None, getattr(request.state, "request_id", None))

        detail = exc.detail
        if isinstance(detail, str):
            payload: dict[str, Any] = {"error": detail}
        elif isinstance(detail, Mapping):
            payload = dict(detail)
        else:
            payload = {"error": "Request failed"}

        if request_id is not None and "request_id" not in payload:
            payload["request_id"] = request_id

        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _exc: Exception) -> JSONResponse:
        payload = {"error": "Unexpected adapter error"}
        request_id = cast(str | None, getattr(request.state, "request_id", None))
        if request_id is not None:
            payload["request_id"] = request_id
        return JSONResponse(status_code=500, content=payload)

    @app.get("/__opentrap/health")
    async def opentrap_health() -> dict[str, object]:
        return {"ok": True, "trap_ids": list(metadata.trap_ids)}

    router = APIRouter()
    for route in routes:
        methods = [method.value for method in route.methods]

        def _build_endpoint(route_spec: RouteSpec) -> Callable[[Request], Awaitable[Response]]:
            async def endpoint(request: Request) -> Response:
                return await dispatch_route(app=app, request=request, route=route_spec)

            return endpoint

        router.add_api_route(route.path, _build_endpoint(route), methods=methods, name=route.name)

    app.include_router(router)
    return app
