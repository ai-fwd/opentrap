# OpenTrap adapter loader tests: verify generated module loading and fail-fast contract checks.
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


def test_load_generated_adapter_loads_routes_upstreams_and_product(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "acme-client"
    generated_dir.mkdir(parents=True)

    _write_generated_module(
        generated_dir / "handlers.py",
        """
        from __future__ import annotations

        from fastapi.responses import JSONResponse
        from opentrap.adapter import RequestContext


        async def intercept(ctx: RequestContext):
            return JSONResponse({"session_id": ctx.session_id})
        """,
    )
    _write_generated_module(
        generated_dir / "routes.py",
        """
        from __future__ import annotations

        from http import HTTPMethod

        from handlers import intercept
        from opentrap.adapter import RouteSpec


        def get_routes() -> list[RouteSpec]:
            return [
                RouteSpec(
                    name="hello",
                    path="/hello",
                    methods=(HTTPMethod.GET,),
                    mode="intercept",
                    handler=intercept,
                )
            ]
        """,
    )
    _write_generated_module(
        generated_dir / "upstreams.py",
        """
        from __future__ import annotations

        from opentrap.adapter import UpstreamSpec


        def get_upstreams() -> list[UpstreamSpec]:
            return [UpstreamSpec(name="origin", base_url="https://origin.test")]
        """,
    )

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root, product_under_test="acme-client")

    loaded = load_generated_adapter(manifest_path)

    assert loaded.product == "acme-client"
    assert loaded.generated_dir == generated_dir
    assert isinstance(loaded.routes[0], RouteSpec)
    assert loaded.routes[0].methods == (HTTPMethod.GET,)
    assert isinstance(loaded.upstreams[0], UpstreamSpec)


def test_load_generated_adapter_fails_when_required_file_is_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)

    _write_generated_module(generated_dir / "routes.py", "def get_routes():\n    return []\n")
    _write_generated_module(generated_dir / "upstreams.py", "def get_upstreams():\n    return []\n")

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(RuntimeError, match="generated adapter file was not found"):
        load_generated_adapter(manifest_path)


def test_load_generated_adapter_fails_for_invalid_factory_return_type(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    generated_dir = repo_root / "adapter" / "generated" / "default"
    generated_dir.mkdir(parents=True)

    _write_generated_module(generated_dir / "handlers.py", "from __future__ import annotations\n")
    _write_generated_module(
        generated_dir / "routes.py",
        """
        from __future__ import annotations


        def get_routes():
            return "not-a-list"
        """,
    )
    _write_generated_module(generated_dir / "upstreams.py", "def get_upstreams():\n    return []\n")

    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path, repo_root=repo_root)

    with pytest.raises(RuntimeError, match=r"generated routes.get_routes\(\) must return a list"):
        load_generated_adapter(manifest_path)
