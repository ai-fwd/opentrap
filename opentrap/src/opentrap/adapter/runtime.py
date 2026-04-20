from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http import HTTPMethod
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol, cast

import httpx
import uvicorn
import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

RouteMode = Literal["intercept", "passthrough", "observe"]
EventEmitter = Callable[[str, Mapping[str, Any]], None]
STATUS_PREFIX = "[adapter]"
_GENERATED_MODULE_NAMES = ("handlers",)
_GENERATED_REQUIRED_FILES = ("handlers.py", "adapter.yaml")
_ROUTE_NAME_PATTERN = re.compile(r"[^a-z0-9]+")


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


class UpstreamRegistry:
    def __init__(self) -> None:
        self._items: dict[str, UpstreamSpec] = {}

    def add(self, *, name: str, base_url: str) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise RuntimeError("upstream name cannot be empty")

        normalized_base_url = base_url.strip()
        if not normalized_base_url:
            raise RuntimeError(f"upstream '{normalized_name}' base_url cannot be empty")

        if normalized_name in self._items:
            raise RuntimeError(f"duplicate upstream name: {normalized_name}")

        self._items[normalized_name] = UpstreamSpec(
            name=normalized_name,
            base_url=normalized_base_url,
        )

    def list(self) -> list[UpstreamSpec]:
        return list(self._items.values())


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


@dataclass(frozen=True)
class LoadedGeneratedAdapter:
    product: str
    generated_dir: Path
    routes: list[RouteSpec]
    upstreams: list[UpstreamSpec]


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


def _status(message: str) -> None:
    print(f"{STATUS_PREFIX} {message}", file=sys.stderr)


def _load_manifest_payload(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("manifest payload must be a JSON object")
    return payload


def _resolve_repo_root(payload: Mapping[str, Any]) -> Path:
    repo_root = payload.get("repo_root")
    if isinstance(repo_root, str) and repo_root.strip():
        return Path(repo_root)
    return Path.cwd()


def _resolve_product_under_test(payload: Mapping[str, Any]) -> str:
    raw_product = payload.get("product_under_test")
    if raw_product is None:
        return "default"
    if not isinstance(raw_product, str) or not raw_product.strip():
        raise RuntimeError("manifest.product_under_test must be a non-empty string when present")
    product = raw_product.strip()
    if product in {".", ".."} or "/" in product or "\\" in product:
        raise RuntimeError("manifest.product_under_test must not contain path separators")
    return product


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
    payload = _load_manifest_payload(manifest_path)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

    manifest = _load_manifest_view(manifest_path, payload)
    trap_ids = tuple(trap.trap_id for trap in manifest.traps)

    return _RuntimeMetadata(run_id=run_id, trap_ids=trap_ids, manifest=manifest)


def _generated_adapter_dir(manifest_path: Path) -> tuple[str, Path]:
    payload = _load_manifest_payload(manifest_path)
    repo_root = _resolve_repo_root(payload)
    product = _resolve_product_under_test(payload)
    return product, repo_root / "adapter" / "generated" / product


def _require_generated_files(generated_dir: Path) -> None:
    if not generated_dir.exists() or not generated_dir.is_dir():
        raise RuntimeError(
            f"generated adapter directory was not found at {generated_dir}"
        )

    for filename in _GENERATED_REQUIRED_FILES:
        file_path = generated_dir / filename
        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError(
                f"generated adapter file was not found: {file_path}"
            )


@contextmanager
def _generated_import_scope(generated_dir: Path) -> Iterator[None]:
    previous_path = list(sys.path)
    previous_modules: dict[str, ModuleType | None] = {
        name: cast(ModuleType | None, sys.modules.get(name))
        for name in _GENERATED_MODULE_NAMES
    }

    try:
        sys.path.insert(0, str(generated_dir))
        for module_name in _GENERATED_MODULE_NAMES:
            sys.modules.pop(module_name, None)
        importlib.invalidate_caches()
        yield
    finally:
        for module_name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = module
        sys.path = previous_path


def _import_generated_module(module_name: str, generated_dir: Path) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to import generated adapter module '{module_name}.py' "
            f"from {generated_dir}: {exc}"
        ) from exc


def _load_adapter_config_payload(config_path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("generated adapter config 'adapter.yaml' must be a mapping")
    return payload


def _normalize_route_name(name: str) -> str:
    normalized = _ROUTE_NAME_PATTERN.sub("_", name.strip().lower()).strip("_")
    if not normalized:
        raise RuntimeError(f"route name '{name}' cannot be normalized to a handler suffix")
    return normalized


def _coerce_string_field(*, value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"generated adapter route '{field_name}' must be a non-empty string")
    return value.strip()


def _coerce_route_mode(value: object) -> RouteMode:
    if not isinstance(value, str):
        raise RuntimeError("generated adapter route 'mode' must be a string")

    mode = value.strip().lower()
    if mode not in {"intercept", "passthrough", "observe"}:
        raise RuntimeError(f"generated adapter route has unsupported mode: {value}")
    return cast(RouteMode, mode)


def _coerce_http_methods(*, route_name: str, value: object) -> tuple[HTTPMethod, ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"route '{route_name}' methods must be a non-empty list")

    methods: list[HTTPMethod] = []
    for raw_method in value:
        if not isinstance(raw_method, str) or not raw_method.strip():
            raise RuntimeError(f"route '{route_name}' has invalid HTTP method: {raw_method!r}")

        method_value = raw_method.strip().upper()
        try:
            methods.append(HTTPMethod(method_value))
        except ValueError as exc:
            raise RuntimeError(
                f"route '{route_name}' has unsupported HTTP method: {raw_method}"
            ) from exc

    return tuple(methods)


def _resolve_route_handler(
    *,
    handlers_module: ModuleType,
    route: RouteSpec,
) -> InterceptHandler | ObserveHandler | None:
    normalized_name = _normalize_route_name(route.name)

    if route.mode == "passthrough":
        return None

    if route.mode == "intercept":
        handler_name = f"intercept_{normalized_name}"
        handler = getattr(handlers_module, handler_name, None)
        if handler is None:
            raise RuntimeError(
                "generated handlers.py is missing required handler "
                f"'{handler_name}' for route '{route.name}'"
            )
        return cast(InterceptHandler, handler)

    handler_name = f"observe_{normalized_name}"
    observer = getattr(handlers_module, handler_name, None)
    if observer is None:
        return None
    return cast(ObserveHandler, observer)


def _build_upstreams_from_config(value: object) -> list[UpstreamSpec]:
    if not isinstance(value, dict):
        raise RuntimeError("generated adapter config field 'upstreams' must be a mapping")

    registry = UpstreamRegistry()
    for key, raw_base_url in value.items():
        if not isinstance(key, str):
            raise RuntimeError("generated adapter upstream keys must be strings")
        if not isinstance(raw_base_url, str):
            raise RuntimeError(f"upstream '{key}' value must be a string URL")
        registry.add(name=key, base_url=raw_base_url)

    return registry.list()


def _build_routes_from_config(
    value: object,
    *,
    handlers_module: ModuleType,
) -> list[RouteSpec]:
    if not isinstance(value, list):
        raise RuntimeError("generated adapter config field 'routes' must be a list")

    routes: list[RouteSpec] = []
    for raw_route in value:
        if not isinstance(raw_route, dict):
            raise RuntimeError("generated adapter route entries must be mappings")

        name = _coerce_string_field(value=raw_route.get("name"), field_name="name")
        path = _coerce_string_field(value=raw_route.get("path"), field_name="path")
        mode = _coerce_route_mode(raw_route.get("mode"))
        methods = _coerce_http_methods(route_name=name, value=raw_route.get("methods"))

        raw_upstream = raw_route.get("upstream")
        upstream: str | None
        if raw_upstream is None:
            upstream = None
        elif isinstance(raw_upstream, str) and raw_upstream.strip():
            upstream = raw_upstream.strip()
        else:
            raise RuntimeError(f"route '{name}' upstream must be a non-empty string when provided")

        raw_upstream_path = raw_route.get("upstream_path")
        upstream_path: str | None
        if raw_upstream_path is None:
            upstream_path = None
        elif isinstance(raw_upstream_path, str) and raw_upstream_path.strip():
            upstream_path = raw_upstream_path.strip()
        else:
            raise RuntimeError(f"route '{name}' upstream_path must be a non-empty string")

        if mode == "intercept" and upstream is not None:
            raise RuntimeError(f"route '{name}' in intercept mode must not declare upstream")
        if mode in {"passthrough", "observe"} and upstream is None:
            raise RuntimeError(f"route '{name}' in {mode} mode requires upstream")

        route_without_handler = RouteSpec(
            name=name,
            path=path,
            methods=methods,
            mode=mode,
            upstream=upstream,
            handler=None,
            upstream_path=upstream_path,
        )

        handler = _resolve_route_handler(
            handlers_module=handlers_module,
            route=route_without_handler,
        )
        routes.append(
            RouteSpec(
                name=name,
                path=path,
                methods=methods,
                mode=mode,
                upstream=upstream,
                handler=handler,
                upstream_path=upstream_path,
            )
        )

    return routes


def load_generated_adapter(manifest_path: Path) -> LoadedGeneratedAdapter:
    product, generated_dir = _generated_adapter_dir(manifest_path)
    _require_generated_files(generated_dir)

    with _generated_import_scope(generated_dir):
        handlers_module = _import_generated_module("handlers", generated_dir)

    config_path = generated_dir / "adapter.yaml"
    config_payload = _load_adapter_config_payload(config_path)

    routes_value = config_payload.get("routes")
    if routes_value is None:
        raise RuntimeError("generated adapter config missing required 'routes' section")

    upstreams_value = config_payload.get("upstreams")
    if upstreams_value is None:
        raise RuntimeError("generated adapter config missing required 'upstreams' section")

    upstreams = _build_upstreams_from_config(upstreams_value)
    routes = _build_routes_from_config(routes_value, handlers_module=handlers_module)
    upstream_map = _build_upstream_map(upstreams)
    _validate_route_specs(routes, upstream_map)

    return LoadedGeneratedAdapter(
        product=product,
        generated_dir=generated_dir,
        routes=routes,
        upstreams=upstreams,
    )


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

    if route.mode == "intercept":
        intercept_handler = cast(InterceptHandler, route.handler)
        return await intercept_handler(context)

    forwarded_response = await _forward_request(app=app, request=request, route=route)

    if route.mode == "passthrough":
        return forwarded_response

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
                return await _dispatch_route(app=app, request=request, route=route_spec)

            return endpoint

        router.add_api_route(route.path, _build_endpoint(route), methods=methods, name=route.name)

    app.include_router(router)
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenTrap adapter runtime")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    loaded = load_generated_adapter(manifest_path)

    app = create_app(
        manifest_path=manifest_path,
        routes=loaded.routes,
        upstreams=loaded.upstreams,
    )

    _status(
        "Host starting on "
        f"{args.host}:{args.port} for adapter product '{loaded.product}' "
        f"from {loaded.generated_dir}; waiting for signal"
    )

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def _on_stop(_signal_num: int, _frame: object | None) -> None:
        del _signal_num, _frame
        _status("Signal received; flushing and finalizing")
        server.should_exit = True

    previous_handlers = {
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)

    exit_code = 0
    try:
        server.run()
    except Exception as exc:  # noqa: BLE001
        _status(f"Shutdown failure: adapter host failed: {exc}")
        exit_code = 1
    finally:
        signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
        signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
        _status("Shutdown complete")

    return exit_code
