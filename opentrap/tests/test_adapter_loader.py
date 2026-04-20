# OpenTrap adapter loader tests: verify YAML loading and fail-fast contract checks.
from __future__ import annotations

import json
import textwrap
from http import HTTPMethod
from pathlib import Path

import pytest

from opentrap.adapter import RouteSpec, UpstreamSpec, load_generated_adapter


def _write_manifest(path: Path, *, repo_root: Path, product_under_test: str = "default") -> None:
    payload = {
        "run_id": "loader-test-run",
        "repo_root": str(repo_root),
        "product_under_test": product_under_test,
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "reasoning/chain-trap",
                "data_items": [],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_generated_module(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def test_load_generated_adapter_loads_yaml_routes_upstreams_and_product(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "acme-client"
    generated_dir.mkdir(parents=True)

    _write_generated_module(
        generated_dir / "handlers.py",
        """
        from __future__ import annotations

        from fastapi.responses import JSONResponse
        from opentrap.adapter import RequestContext


        async def intercept_hello(ctx: RequestContext):
            return JSONResponse({"session_id": ctx.session_id})


        async def observe_watch(_ctx: RequestContext, _snapshot):
            return None
        """,
    )
    (generated_dir / "adapter.yaml").write_text(
        textwrap.dedent(
            """
            routes:
              - name: hello
                path: /hello
                methods: [GET]
                mode: intercept
              - name: mirror
                path: /mirror/{item_id}
                methods: [POST]
                mode: passthrough
                upstream: origin
                upstream_path: /backend/{item_id}
              - name: watch
                path: /watch
                methods: [GET]
                mode: observe
                upstream: origin
            upstreams:
              origin: https://origin.test
            """
        ),
        encoding="utf-8",
    )

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root, product_under_test="acme-client")

    loaded = load_generated_adapter(manifest_path)

    assert loaded.product == "acme-client"
    assert loaded.generated_dir == generated_dir
    assert len(loaded.routes) == 3
    assert isinstance(loaded.routes[0], RouteSpec)
    assert loaded.routes[0].methods == (HTTPMethod.GET,)
    assert isinstance(loaded.upstreams[0], UpstreamSpec)


def test_load_generated_adapter_fails_when_required_file_is_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)
    _write_generated_module(generated_dir / "handlers.py", "from __future__ import annotations\n")

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(RuntimeError, match="generated adapter file was not found"):
        load_generated_adapter(manifest_path)


@pytest.mark.parametrize(
    ("yaml_body", "error_match"),
    [
        (
            """
            routes:
              - name: hello
                path: /hello
                methods: [WRONG]
                mode: intercept
            upstreams:
              origin: https://origin.test
            """,
            "unsupported HTTP method",
        ),
        (
            """
            routes:
              - name: hello
                path: /hello
                methods: [GET]
                mode: unknown
            upstreams:
              origin: https://origin.test
            """,
            "unsupported mode",
        ),
    ],
)
def test_load_generated_adapter_fails_for_invalid_route_fields(
    tmp_path: Path,
    yaml_body: str,
    error_match: str,
) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)
    _write_generated_module(
        generated_dir / "handlers.py",
        """
        from __future__ import annotations

        from fastapi.responses import JSONResponse
        from opentrap.adapter import RequestContext


        async def intercept_hello(_ctx: RequestContext):
            return JSONResponse({"ok": True})
        """,
    )
    (generated_dir / "adapter.yaml").write_text(textwrap.dedent(yaml_body), encoding="utf-8")

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(RuntimeError, match=error_match):
        load_generated_adapter(manifest_path)


def test_load_generated_adapter_fails_for_unknown_upstream_key(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)
    _write_generated_module(generated_dir / "handlers.py", "from __future__ import annotations\n")
    (generated_dir / "adapter.yaml").write_text(
        textwrap.dedent(
            """
            routes:
              - name: mirror
                path: /mirror
                methods: [GET]
                mode: passthrough
                upstream: missing
            upstreams:
              origin: https://origin.test
            """
        ),
        encoding="utf-8",
    )

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(ValueError, match="unknown upstream"):
        load_generated_adapter(manifest_path)


def test_load_generated_adapter_fails_when_intercept_handler_is_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)
    _write_generated_module(generated_dir / "handlers.py", "from __future__ import annotations\n")
    (generated_dir / "adapter.yaml").write_text(
        textwrap.dedent(
            """
            routes:
              - name: hello-world
                path: /hello
                methods: [GET]
                mode: intercept
            upstreams: {}
            """
        ),
        encoding="utf-8",
    )

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(RuntimeError, match="missing required handler"):
        load_generated_adapter(manifest_path)


def test_runtime_module_reexports_expected_public_symbols() -> None:
    import opentrap.adapter.runtime as runtime_module

    expected_names = (
        "create_app",
        "load_generated_adapter",
        "build_parser",
        "main",
        "RequestContext",
        "RouteSpec",
        "UpstreamSpec",
        "DataItems",
    )
    for name in expected_names:
        assert hasattr(runtime_module, name)
