# OpenTrap adapter runtime unit tests.
# Verifies route validation, dispatch behavior, and request context/data item access.
from __future__ import annotations

import json
from http import HTTPMethod
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from opentrap.adapter import RequestContext, RouteSpec, UpstreamSpec, create_app
from opentrap.adapter.default_handlers import observe_openai_responses_default
from opentrap.execution_context import ActiveSessionDescriptor, write_active_session_descriptor

TEST_SESSION_ID = "test-session-id"


def _write_manifest(path: Path) -> None:
    repo_root = path.parent
    payload = {
        "run_id": "test-run-id",
        "repo_root": str(repo_root),
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_case_index": None,
        "active_session_id": TEST_SESSION_ID,
        "sessions_file": "sessions.jsonl",
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
                "cases": [
                    {
                        "case_index": 0,
                        "item_id": "00001",
                        "data_item": {
                            "id": "00001",
                            "path": "dataset/item-00001.txt",
                        },
                        "metadata": {"item_id": "00001"},
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _write_active_session(
        path,
        case={
            "case_index": 0,
            "item_id": "00001",
            "data_item": {"id": "00001", "path": "dataset/item-00001.txt"},
            "metadata": {"item_id": "00001"},
        },
    )


def _write_active_session(manifest_path: Path, *, case: dict[str, Any]) -> ActiveSessionDescriptor:
    session_path = manifest_path.parent / "sessions.jsonl"
    evidence_path = manifest_path.parent / "traces.jsonl"
    session_payload = {
        "run_id": "test-run-id",
        "session_id": TEST_SESSION_ID,
        "case_index": int(case.get("case_index", 0)),
        "item_id": case.get("item_id"),
        "started_at_utc": "2026-01-01T00:00:00+00:00",
        "ended_at_utc": None,
        "harness_exit_code": None,
    }
    session_path.write_text(json.dumps(session_payload) + "\n", encoding="utf-8")
    evidence_path.touch(exist_ok=True)
    descriptor = ActiveSessionDescriptor(
        run_id="test-run-id",
        session_id=TEST_SESSION_ID,
        case_index=int(case.get("case_index", 0)),
        session_path=session_path,
        evidence_path=evidence_path,
        case=dict(case),
    )
    write_active_session_descriptor(manifest_path.parent / "active_session.json", descriptor)
    return descriptor


def _read_evidence(manifest_path: Path) -> list[dict[str, Any]]:
    evidence_path = manifest_path.parent / "traces.jsonl"
    return [
        json.loads(line)
        for line in evidence_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_observations(manifest_path: Path) -> list[dict[str, Any]]:
    observations_path = manifest_path.parent / "observations.jsonl"
    if not observations_path.exists():
        return []
    return [
        json.loads(line)
        for line in observations_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_observe_route(
    *,
    manifest_path: Path,
    route_name: str,
    route_path: str,
    observer: Any,
    transport_handler: Any,
) -> Response:
    forward_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name=route_name,
                path=route_path,
                methods=(HTTPMethod.GET,),
                mode="observe",
                handler=observer,
                upstream="origin",
            )
        ],
        upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
        forward_client=forward_client,
    )

    with TestClient(app) as client:
        return client.get(route_path)


def test_health_route_starts_and_ends_runtime_session(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    app = create_app(
        manifest_path=manifest_path,
        routes=[],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/__opentrap/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "trap_ids": ["reasoning/chain-trap"]}


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
        )


def test_intercept_handler_receives_minimal_request_context(tmp_path: Path) -> None:
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
    )

    with TestClient(app) as client:
        response = client.get("/hello")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "session_id": TEST_SESSION_ID}

    context = captured["ctx"]
    assert isinstance(context.request, Request)
    assert context.run_id == "test-run-id"
    assert context.session_id == TEST_SESSION_ID
    assert context.request_id
    assert context.manifest.manifest_path == manifest_path
    assert context.manifest.requested == "reasoning/chain-trap"
    assert context.manifest.traps[0].trap_id == "reasoning/chain-trap"


def test_passthrough_route_forwards_to_named_upstream(tmp_path: Path) -> None:
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

    response = _run_observe_route(
        manifest_path=manifest_path,
        route_name="observe",
        route_path="/observe",
        observer=observer,
        transport_handler=transport_handler,
    )

    assert response.status_code == 200
    assert response.text == "upstream"
    assert response.headers["x-origin"] == "1"
    assert "x-observer-mutated" not in response.headers
    assert calls["count"] == 1


def test_observe_handler_payload_is_emitted_by_runtime(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def observer(ctx: RequestContext, snapshot: Response):
        return {
            "model": "gpt-4.1-mini",
            "request_id": ctx.request_id,
            "status_code": snapshot.status_code,
            "ignored_field": "from-observer",
        }

    async def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=201,
            headers={"x-origin": "1", "content-type": "application/json"},
            json={"ok": True},
        )

    response = _run_observe_route(
        manifest_path=manifest_path,
        route_name="observe-emits",
        route_path="/observe-emits",
        observer=observer,
        transport_handler=transport_handler,
    )

    assert response.status_code == 201
    observed_events = [
        event for event in _read_evidence(manifest_path) if event["event_type"] == "llm_responses_observed"
    ]
    assert len(observed_events) == 1
    assert observed_events[0]["model"] == "gpt-4.1-mini"
    assert observed_events[0]["status_code"] == 201
    assert "ignored_field" not in observed_events[0]


def test_default_openai_observer_writes_llm_observation(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "hello "},
                            {"type": "output_text", "text": "world"},
                        ],
                    }
                ]
            },
        )

    forward_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="openai-responses",
                path="/v1/responses",
                methods=(HTTPMethod.POST,),
                mode="observe",
                handler=observe_openai_responses_default,
                upstream="origin",
            )
        ],
        upstreams=[UpstreamSpec(name="origin", base_url="https://origin.test")],
        forward_client=forward_client,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            json={"model": "gpt-4.1-mini", "input": "hello"},
        )

    assert response.status_code == 200
    observations = _read_observations(manifest_path)
    assert len(observations) == 1
    observation = observations[0]
    assert observation["case_index"] == 0
    assert observation["session_id"] == TEST_SESSION_ID
    assert observation["request_id"]
    assert observation["observation_type"] == "llm.response"
    assert observation["content_type"] == "text/plain"
    assert observation["content"] == "hello world"
    assert observation["model"] == "gpt-4.1-mini"
    assert observation["status_code"] == 200
    assert observation["timestamp_utc"]


def test_default_openai_observer_skips_observation_when_no_extractable_text(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "refusal", "refusal": "cannot comply"},
                        ],
                    }
                ]
            },
        )

    response = _run_observe_route(
        manifest_path=manifest_path,
        route_name="openai-responses-no-text",
        route_path="/v1/responses-no-text",
        observer=observe_openai_responses_default,
        transport_handler=transport_handler,
    )

    assert response.status_code == 200
    assert _read_observations(manifest_path) == []


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
        )


def test_request_context_has_no_data_items_or_emit_event(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def intercept_handler(ctx: RequestContext) -> Response:
        return JSONResponse(
            {
                "has_data_items": hasattr(ctx, "data_items"),
                "has_emit_event": hasattr(ctx, "emit_event"),
                "has_trap_actions": hasattr(ctx, "trap_actions"),
                "trap_actions_is_none": ctx.trap_actions is None,
            }
        )

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="ctx-contract",
                path="/ctx-contract",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/ctx-contract")

    assert response.status_code == 200
    assert response.json() == {
        "has_data_items": False,
        "has_emit_event": False,
        "has_trap_actions": True,
        "trap_actions_is_none": True,
    }


def test_trap_binding_binds_perception_actions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    data_file = (
        repo_root
        / ".opentrap"
        / "dataset"
        / "perception"
        / "prompt_injection_via_html"
        / "artifact"
        / "data"
        / "00001.htm"
    )
    data_file.parent.mkdir(parents=True)
    data_file.write_text("<html>hello from trap actions</html>", encoding="utf-8")

    manifest_path = tmp_path / "run.json"
    payload = {
        "run_id": "test-run-id",
        "repo_root": str(repo_root),
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "reasoning/chain-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "perception/prompt_injection_via_html",
                "data_dir": ".opentrap/dataset/perception/prompt_injection_via_html/artifact/data",
                "data_items": [
                    {
                        "id": "00001",
                        "path": (
                            ".opentrap/dataset/perception/prompt_injection_via_html/artifact/data/00001.htm"
                        ),
                    }
                ],
            }
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    _write_active_session(
        manifest_path,
        case={
            "case_index": 0,
            "item_id": "00001",
            "data_item": {
                "id": "00001",
                "path": str(
                    repo_root
                    / ".opentrap"
                    / "dataset"
                    / "perception"
                    / "prompt_injection_via_html"
                    / "artifact"
                    / "data"
                    / "00001.htm"
                ),
            },
            "metadata": {"item_id": "00001"},
        },
    )

    async def intercept_handler(ctx: RequestContext) -> Response:
        assert ctx.trap_actions is not None
        content = ctx.trap_actions.get_current_data()
        return JSONResponse({"content": content})

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="read-item",
                path="/read-item",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/read-item")

    assert response.status_code == 200
    assert response.json() == {"content": "<html>hello from trap actions</html>"}


def test_request_context_manifest_fields_are_typed(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    artifact_path = repo_root / ".opentrap" / "dataset" / "generic" / "artifact"
    data_dir = artifact_path / "data"
    metadata_path = artifact_path / "metadata.jsonl"
    data_dir.mkdir(parents=True)
    metadata_path.write_text("", encoding="utf-8")

    manifest_path = tmp_path / "run.json"
    payload = {
        "run_id": "test-run-id",
        "repo_root": str(repo_root),
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "requested": "generic/document-trap",
        "status": "armed",
        "scorer_status": "pending",
        "active_session_id": None,
        "sessions": [],
        "traps": [
            {
                "trap_id": "generic/document-trap",
                "artifact_path": ".opentrap/dataset/generic/artifact",
                "metadata_path": ".opentrap/dataset/generic/artifact/metadata.jsonl",
                "data_dir": ".opentrap/dataset/generic/artifact/data",
                "data_items": [
                    {
                        "id": "00001",
                        "path": ".opentrap/dataset/generic/artifact/data/00001.txt",
                    }
                ],
            }
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    _write_active_session(
        manifest_path,
        case={
            "case_index": 0,
            "item_id": "00001",
            "data_item": {
                "id": "00001",
                "path": str(
                    repo_root
                    / ".opentrap"
                    / "dataset"
                    / "generic"
                    / "artifact"
                    / "data"
                    / "00001.txt"
                ),
            },
            "metadata": {"item_id": "00001"},
        },
    )

    async def intercept_handler(ctx: RequestContext) -> Response:
        trap = ctx.manifest.traps[0]
        return JSONResponse(
            {
                "requested": ctx.manifest.requested,
                "manifest_path": str(ctx.manifest.manifest_path),
                "repo_root": str(ctx.manifest.repo_root),
                "trap_id": trap.trap_id,
                "artifact_path": str(trap.artifact_path),
                "metadata_path": str(trap.metadata_path),
                "data_dir": str(trap.data_dir),
                "trap_actions_is_none": ctx.trap_actions is None,
            }
        )

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="typed-context",
                path="/typed-context",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/typed-context")

    assert response.status_code == 200
    assert response.json() == {
        "requested": "generic/document-trap",
        "manifest_path": str(manifest_path),
        "repo_root": str(repo_root),
        "trap_id": "generic/document-trap",
        "artifact_path": str(artifact_path),
        "metadata_path": str(metadata_path),
        "data_dir": str(data_dir),
        "trap_actions_is_none": True,
    }


def test_route_dispatch_events_are_emitted_for_intercept(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def intercept_handler(_ctx: RequestContext) -> Response:
        return JSONResponse({"ok": True})

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="dispatch-events",
                path="/dispatch-events",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/dispatch-events")

    assert response.status_code == 200
    event_types = [event["event_type"] for event in _read_evidence(manifest_path)]
    assert "route_dispatch_pre" in event_types
    assert "route_dispatch_post" in event_types
    assert "http_exchange" not in event_types

    expected_pre_keys = {
        "case_index",
        "event_type",
        "route_name",
        "route_mode",
        "method",
        "path",
        "query",
        "status_code",
        "duration",
    }
    for event in _read_evidence(manifest_path):
        if event["event_type"] == "route_dispatch_pre":
            assert set(event.keys()) == expected_pre_keys


def test_traces_are_grouped_by_case_index_in_run_order(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)
    traces_path = manifest_path.parent / "traces.jsonl"
    traces_path.write_text("", encoding="utf-8")

    async def intercept_handler(_ctx: RequestContext) -> Response:
        return JSONResponse({"ok": True})

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="dispatch-events",
                path="/dispatch-events",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
                upstream=None,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        for case_index in (0, 1, 2):
            _write_active_session(
                manifest_path,
                case={
                    "case_index": case_index,
                    "item_id": "00001",
                    "data_item": {"id": "00001", "path": "dataset/item-00001.txt"},
                    "metadata": {"item_id": "00001"},
                },
            )
            response = client.get("/dispatch-events")
            assert response.status_code == 200

    case_indexes = [event["case_index"] for event in _read_evidence(manifest_path)]
    assert case_indexes == [0, 0, 1, 1, 2, 2]


def test_request_context_helper_methods_parse_path_and_body(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def intercept_handler(ctx: RequestContext) -> Response:
        item_id = ctx.path_param("item_id")
        missing = ctx.path_param("missing", required=False)
        body_text = await ctx.body_text()
        body_bytes = await ctx.body_bytes()
        return JSONResponse(
            {
                "item_id": item_id,
                "missing": missing,
                "body_text": body_text,
                "body_size": len(body_bytes),
            }
        )

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="helpers",
                path="/helpers/{item_id}",
                methods=(HTTPMethod.POST,),
                mode="intercept",
                handler=intercept_handler,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.post("/helpers/abc", content=b"hello")

    assert response.status_code == 200
    assert response.json() == {
        "item_id": "abc",
        "missing": None,
        "body_text": "hello",
        "body_size": 5,
    }


def test_request_context_json_body_invalid_uses_http_exception_handler(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def intercept_handler(ctx: RequestContext) -> Response:
        await ctx.json_body()
        return JSONResponse({"ok": True})

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="json-body",
                path="/json-body",
                methods=(HTTPMethod.POST,),
                mode="intercept",
                handler=intercept_handler,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.post("/json-body", content="not-json")

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "Request body must be valid JSON"
    assert payload["request_id"]


def test_http_exception_handler_includes_request_id(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run.json"
    _write_manifest(manifest_path)

    async def intercept_handler(_ctx: RequestContext) -> Response:
        raise HTTPException(status_code=418, detail="teapot")

    app = create_app(
        manifest_path=manifest_path,
        routes=[
            RouteSpec(
                name="teapot",
                path="/teapot",
                methods=(HTTPMethod.GET,),
                mode="intercept",
                handler=intercept_handler,
            )
        ],
        upstreams=[],
    )

    with TestClient(app) as client:
        response = client.get("/teapot")

    assert response.status_code == 418
    payload = response.json()
    assert payload["error"] == "teapot"
    assert payload["request_id"]
