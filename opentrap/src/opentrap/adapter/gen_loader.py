from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPMethod
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import yaml

from .http_runtime import build_upstream_map, validate_route_specs
from .manifest import generated_adapter_dir
from .models import (
    InterceptHandler,
    LoadedGeneratedAdapter,
    ObserveHandler,
    RouteMode,
    RouteSpec,
    UpstreamSpec,
)

_GENERATED_MODULE_NAMES = ("handlers",)
_GENERATED_REQUIRED_FILES = ("handlers.py", "adapter.yaml")
_ROUTE_NAME_PATTERN = re.compile(r"[^a-z0-9]+")


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
    product, generated_dir = generated_adapter_dir(manifest_path)
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
    upstream_map = build_upstream_map(upstreams)
    validate_route_specs(routes, upstream_map)

    return LoadedGeneratedAdapter(
        product=product,
        generated_dir=generated_dir,
        routes=routes,
        upstreams=upstreams,
    )
