from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from http import HTTPMethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Response

RouteMode = Literal["intercept", "passthrough", "observe"]

if TYPE_CHECKING:
    from .context import RequestContext
else:
    RequestContext = Any

InterceptHandler = Callable[[RequestContext], Awaitable[Response]]
ObserveHandler = Callable[[RequestContext, Response], Awaitable[Mapping[str, Any] | None]]


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
