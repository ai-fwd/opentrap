from __future__ import annotations

import json
from http import HTTPMethod
from pathlib import Path
from typing import Any

import httpx
import pytest
from adapter.host import RequestContext, RouteSpec, UpstreamSpec, create_app
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[Path] = []
        self.ended = 0
        self.events: list[tuple[str, dict[str, Any]]] = []

    def start_session(self, manifest_path: str | Path) -> str:
        self.started.append(Path(manifest_path))
        return "test-session-id"

    def end_session(self) -> None:
        self.ended += 1

    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))

    def list_data_items(self) -> list[object]:
        return []

    def get_data_item(self, item_id: str) -> object:
        raise KeyError(item_id)


def _write_manifest(path: Path) -> None:
    payload = {
        "run_id": "test-run-id",
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "reasoning/chain-trap",
                "data_items": [
                    {
                        "id": "00001",
                        "path": "dataset/item-00001.txt",
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_health_route_starts_and_ends_runtime_session(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    app = create_app(
        manifest_path=manifest_path,
        routes=[],
        upstreams=[],
        runtime=runtime,
    )

    with TestClient(app) as client:
        response = client.get("/__opentrap/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "trap_ids": ["reasoning/chain-trap"]}

    assert runtime.started == [manifest_path]
    assert runtime.ended == 1
    assert any(
        event_type == "http_exchange" and payload["path"] == "/__opentrap/health"
        for event_type, payload in runtime.events
    )


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (
            RouteSpec(
                name="missing-handler",
                path="/a",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=None,
                upstream=None,
            ),
            "requires a handler",
        ),
        (
            RouteSpec(
                name="bad-passthrough-handler",
                path="/a",
                methods=(HTTPMethod.GET,),
                mode="passthrough",
                handler=lambda _ctx: JSONResponse({"ok": True}),
                upstream="origin",
            ),
            "must not define a handler",
        ),
        (
            RouteSpec(
                name="missing-passthrough-upstream",
                path="/a",
                methods=(HTTPMethod.GET,),
                mode="passthrough",
                handler=None,
                upstream=None,
            ),
            "requires an upstream",
        ),
        (
            RouteSpec(
                name="missing-observe-upstream",
                path="/a",
                methods=(HTTPMethod.GET,),
                mode="observe",
                handler=None,
                upstream=None,
            ),
            "requires an upstream",
        ),
        (
            RouteSpec(
                name="bad-intercept-upstream",
                path="/a",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=lambda _ctx: JSONResponse({"ok": True}),
                upstream="origin",
            ),
            "must not define an upstream",
        ),
    ],
)
def test_route_mode_rules_are_enforced(route: RouteSpec, expected: str, tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    with pytest.raises(ValueError, match=expected):
        create_app(
            manifest_path=manifest_path,
            routes=[route],
            upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
            runtime=FakeRuntime(),
        )


def test_intercept_handler_receives_minimal_request_context(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    captured: dict[str, RequestContext] = {}

    async def intercept_handler(ctx: RequestContext) -> Response:
        captured["ctx"] = ctx
        return JSONResponse({"ok": True, "session_id": ctx.session_id})

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="hello",
                path="/hello",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
        runtime=runtime,
    )

    with TestClient(app) as client:
        response = client.get("/hello")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "session_id": "test-session-id"}

    context = captured["ctx"]
    assert isinstance(context.request, Request)
    assert context.run_id == "test-run-id"
    assert context.session_id == "test-session-id"
    assert context.request_id


def test_passthrough_route_forwards_to_named_upstream(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    observed: dict[str, str] = {}

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["body"] = request.content.decode("utf-8")
        return httpx.Response(
            status_code=202,
            headers={"x-origin": "1", "content-type": "application/json"},
            json={"forwarded": True},
        )

    forward_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="passthrough",
                path="/mirror/{item_id}",
                methods=(HTTPMethod.POST,),
                mode="passthrough",
                handler=None,
                upstream="origin",
                upstream_path="/backend/{item_id}",
            )
        ],
        upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
        runtime=runtime,
        forward_client=forward_client,
    )

    with TestClient(app) as client:
        response = client.post("/mirror/42?q=yes", content="hello")

    assert response.status_code == 202
    assert response.json() == {"forwarded": True}
    assert observed["method"] == "POST"
    assert observed["url"] == "https://origin.test/backend/42?q=yes"
    assert observed["body"] == "hello"


def test_observe_handler_runs_without_mutating_forwarded_response(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    calls: dict[str, int] = {"count": 0}

    async def observer(_ctx: RequestContext, snapshot: Response) -> None:
        calls["count"] += 1
        snapshot.headers["x-observer-mutated"] = "1"

    async def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"x-origin": "1", "content-type": "text/plain; charset=utf-8"},
            text="upstream",
        )

    forward_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="observe",
                path="/observe",
                methods=(HTTPMethod.GET,),
                mode="observe",
                handler=observer,
                upstream="origin",
            )
        ],
        upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
        runtime=runtime,
        forward_client=forward_client,
    )

    with TestClient(app) as client:
        response = client.get("/observe")

    assert response.status_code == 200
    assert response.text == "upstream"
    assert response.headers["x-origin"] == "1"
    assert "x-observer-mutated" not in response.headers
    assert calls["count"] == 1


def test_unknown_named_upstream_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    route = RouteSpec(
        name="unknown-upstream",
        path="/a",
        methods=(HTTPMethod.GET,),
        mode="passthrough",
        handler=None,
        upstream="missing",
    )

    with pytest.raises(ValueError, match="unknown upstream"):
        create_app(
            manifest_path=manifest_path,
            routes=[route],
            upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
            runtime=FakeRuntime(),
        )
