from __future__ import annotations

import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http import HTTPMethod
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

RouteMode = Literal["intercept", "passthrough", "observe"]
EventEmitter = Callable[[str, Mapping[str, Any]], None]


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


@dataclass(frozen=True)
class UpstreamSpec:
    name: str
    base_url: str


@dataclass(frozen=True)
class DataItemView:
    id: str
    path: Path


@dataclass(frozen=True)
class ManifestTrapView:
    trap_id: str
    artifact_path: Path | None
    metadata_path: Path | None
    data_dir: Path | None
    data_items: tuple[DataItemView, ...]


@dataclass(frozen=True)
class ManifestView:
    manifest_path: Path
    repo_root: Path
    requested: str | None
    traps: tuple[ManifestTrapView, ...]


@dataclass(frozen=True)
class RequestContext:
    request: Request
    run_id: str
    session_id: str
    request_id: str
    manifest: ManifestView
    data_items: DataItems
    _event_emitter: EventEmitter = field(repr=False, compare=False)

    def emit_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._event_emitter(event_type, payload)


InterceptHandler = Callable[[RequestContext], Awaitable[Response]]
ObserveHandler = Callable[[RequestContext, Response], Awaitable[None]]


@dataclass(frozen=True)
class RouteSpec:
    name: str
    path: str
    methods: tuple[HTTPMethod, ...]
    mode: RouteMode
    upstream: str | None = None
    handler: InterceptHandler | ObserveHandler | None = None
    upstream_path: str | None = None


@dataclass(frozen=True)
class _RuntimeMetadata:
    run_id: str
    trap_ids: tuple[str, ...]
    manifest: ManifestView


class DataItems:
    def __init__(self, *, runtime: RuntimeProtocol, base_dir: Path) -> None:
        self._runtime = runtime
        self._base_dir = base_dir

    def list(self) -> tuple[DataItemView, ...]:
        return tuple(
            self._coerce_item(item)
            for item in self._runtime.list_data_items()
        )

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


def _resolve_repo_root(payload: Mapping[str, Any]) -> Path:
    repo_root = payload.get("repo_root")
    if isinstance(repo_root, str) and repo_root.strip():
        return Path(repo_root)
    return Path.cwd()


def _resolve_manifest_path(path_value: object, *, repo_root: Path) -> Path | None:
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return repo_root / path


def _load_manifest_data_items(
    trap_payload: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[DataItemView, ...]:
    raw_items = trap_payload.get("data_items")
    if not isinstance(raw_items, list):
        return ()

    items: list[DataItemView] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item_id = raw_item.get("id")
        path_value = raw_item.get("path")
        if not isinstance(item_id, str) or not item_id:
            continue
        resolved_path = _resolve_manifest_path(path_value, repo_root=repo_root)
        if resolved_path is None:
            continue
        items.append(DataItemView(id=item_id, path=resolved_path))
    return tuple(items)


def _load_manifest_view(manifest_path: Path, payload: Mapping[str, Any]) -> ManifestView:
    repo_root = _resolve_repo_root(payload)
    requested = payload.get("requested")
    requested_value = requested if isinstance(requested, str) and requested else None

    traps_payload = payload.get("traps")
    traps: list[ManifestTrapView] = []
    if isinstance(traps_payload, list):
        for raw_trap in traps_payload:
            if not isinstance(raw_trap, dict):
                continue
            trap_id = raw_trap.get("trap_id")
            if not isinstance(trap_id, str) or not trap_id:
                continue
            traps.append(
                ManifestTrapView(
                    trap_id=trap_id,
                    artifact_path=_resolve_manifest_path(
                        raw_trap.get("artifact_path"),
                        repo_root=repo_root,
                    ),
                    metadata_path=_resolve_manifest_path(
                        raw_trap.get("metadata_path"),
                        repo_root=repo_root,
                    ),
                    data_dir=_resolve_manifest_path(
                        raw_trap.get("data_dir"),
                        repo_root=repo_root,
                    ),
                    data_items=_load_manifest_data_items(raw_trap, repo_root=repo_root),
                )
            )

    return ManifestView(
        manifest_path=manifest_path,
        repo_root=repo_root,
        requested=requested_value,
        traps=tuple(traps),
    )


def _load_manifest_metadata(manifest_path: Path) -> _RuntimeMetadata:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("manifest payload must be a JSON object")

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

    manifest = _load_manifest_view(manifest_path, payload)
    trap_ids = tuple(trap.trap_id for trap in manifest.traps)

    return _RuntimeMetadata(run_id=run_id, trap_ids=trap_ids, manifest=manifest)


def _build_upstream_map(upstreams: Iterable[UpstreamSpec]) -> dict[str, UpstreamSpec]:
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


def _validate_handler_signature(route: RouteSpec) -> None:
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


def _validate_route_specs(
    routes: Iterable[RouteSpec], upstream_map: Mapping[str, UpstreamSpec]
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

        _validate_handler_signature(route)


async def _request_body(request: Request) -> bytes:
    if hasattr(request.state, "raw_body"):
        return cast(bytes, request.state.raw_body)
    raw_body = await request.body()
    request.state.raw_body = raw_body
    return raw_body


def _copy_request_with_body(request: Request, body: bytes) -> Request:
    consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    updated = Request(request.scope, receive)
    updated.state.raw_body = body
    updated.state.request_id = request.state.request_id
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
    )

    return Response(
        content=forwarded.content,
        status_code=forwarded.status_code,
        headers=_filtered_response_headers(dict(forwarded.headers)),
    )


async def _dispatch_route(*, app: FastAPI, request: Request, route: RouteSpec) -> Response:
    context = RequestContext(
        request=request,
        run_id=cast(str, app.state.run_id),
        session_id=cast(str, app.state.session_id),
        request_id=cast(str, request.state.request_id),
        manifest=cast(ManifestView, app.state.manifest),
        data_items=DataItems(
            runtime=cast(RuntimeProtocol, app.state.runtime),
            base_dir=cast(Path, app.state.repo_root),
        ),
        _event_emitter=cast(EventEmitter, app.state.event_emitter),
    )

    # Scenario: intercept routes are fully owned by generated handler logic.
    if route.mode == "intercept":
        intercept_handler = cast(InterceptHandler, route.handler)
        return await intercept_handler(context)

    forwarded_response = await _forward_request(app=app, request=request, route=route)

    # Scenario: passthrough returns upstream response as-is after basic forwarding.
    if route.mode == "passthrough":
        return forwarded_response

    # Scenario: observe routes run side effects after forwarding but never mutate final response.
    observer = cast(ObserveHandler | None, route.handler)
    if observer is not None:
        observer_snapshot = Response(
            content=forwarded_response.body,
            status_code=forwarded_response.status_code,
            headers=dict(forwarded_response.headers),
        )
        await observer(context, observer_snapshot)

    return forwarded_response


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
    metadata = _load_manifest_metadata(manifest_path)
    upstream_map = _build_upstream_map(upstreams)
    _validate_route_specs(routes, upstream_map)

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
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        raw_body = await request.body()
        request_with_body = _copy_request_with_body(request, raw_body)

        response = await call_next(request_with_body)

        response_size = 0
        content_length = response.headers.get("content-length")
        if content_length is not None and content_length.isdigit():
            response_size = int(content_length)
        elif hasattr(response, "body") and isinstance(response.body, bytes | bytearray):
            response_size = len(response.body)

        runtime_impl.emit_event(
            "http_exchange",
            {
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "request_size": len(raw_body),
                "response_size": response_size,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            },
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": "Unexpected adapter error"})

    @app.get("/__opentrap/health")
    async def opentrap_health() -> dict[str, object]:
        return {"ok": True, "trap_ids": list(metadata.trap_ids)}

    for route in routes:
        methods = [method.value for method in route.methods]

        def _build_endpoint(route_spec: RouteSpec) -> Callable[[Request], Awaitable[Response]]:
            async def endpoint(request: Request) -> Response:
                return await _dispatch_route(app=app, request=request, route=route_spec)

            return endpoint

        app.add_api_route(route.path, _build_endpoint(route), methods=methods, name=route.name)

    return app
