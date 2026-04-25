from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentrap.io_utils import load_json_maybe, utc_now_iso, write_json

OBSERVATIONS_FILE_NAME = "observations.jsonl"


@dataclass(frozen=True)
class ActiveSessionDescriptor:
    run_id: str
    session_id: str
    case_index: int
    session_path: Path
    evidence_path: Path
    case: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ActiveSessionDescriptor:
        run_id = payload.get("run_id")
        session_id = payload.get("session_id")
        case_index = payload.get("case_index")
        session_path = payload.get("session_path")
        evidence_path = payload.get("evidence_path")
        case = payload.get("case")

        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("active session descriptor run_id must be a non-empty string")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("active session descriptor session_id must be a non-empty string")
        if not isinstance(case_index, int) or case_index < 0:
            raise RuntimeError(
                "active session descriptor case_index must be a non-negative integer"
            )
        if not isinstance(session_path, str) or not session_path:
            raise RuntimeError("active session descriptor session_path must be a non-empty string")
        if not isinstance(evidence_path, str) or not evidence_path:
            raise RuntimeError("active session descriptor evidence_path must be a non-empty string")
        if not isinstance(case, Mapping):
            raise RuntimeError("active session descriptor case must be an object")

        return cls(
            run_id=run_id,
            session_id=session_id,
            case_index=case_index,
            session_path=Path(session_path),
            evidence_path=Path(evidence_path),
            case=dict(case),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "case_index": self.case_index,
            "session_path": str(self.session_path),
            "evidence_path": str(self.evidence_path),
            "case": dict(self.case),
        }


_current_execution_context: ContextVar[ActiveSessionDescriptor | None] = ContextVar(
    "opentrap_current_execution_context",
    default=None,
)


def active_session_path_for_run(run_dir: Path) -> Path:
    return run_dir / "active_session.json"


def load_active_session_descriptor(path: Path) -> ActiveSessionDescriptor | None:
    payload = load_json_maybe(path)
    if payload is None:
        return None
    return ActiveSessionDescriptor.from_payload(payload)


def write_active_session_descriptor(path: Path, descriptor: ActiveSessionDescriptor) -> None:
    write_json(path, descriptor.as_payload(), atomic=True)


def clear_active_session_descriptor(path: Path) -> None:
    path.unlink(missing_ok=True)


@contextmanager
def bind_execution_context(descriptor: ActiveSessionDescriptor) -> Iterator[None]:
    token: Token[ActiveSessionDescriptor | None] = _current_execution_context.set(descriptor)
    try:
        yield
    finally:
        _current_execution_context.reset(token)


def get_current_execution_context() -> ActiveSessionDescriptor:
    descriptor = _current_execution_context.get()
    if descriptor is None:
        raise RuntimeError("no active execution context is bound to the current request")
    return descriptor


def emit_event(
    *,
    execution_context: ActiveSessionDescriptor,
    event_type: str,
    payload: Mapping[str, Any],
) -> None:
    if not isinstance(event_type, str) or not event_type.strip():
        raise RuntimeError("event_type must be a non-empty string")

    route_name = payload.get("route_name")
    route_mode = payload.get("route_mode")
    method = payload.get("method")
    path = payload.get("path")
    query = payload.get("query")
    status_code = payload.get("status_code")
    duration = payload.get("duration")
    model = payload.get("model")
    request_id = payload.get("request_id")

    trace_row: dict[str, Any] = {
        "case_index": execution_context.case_index,
        "request_id": request_id if isinstance(request_id, str) else None,
        "event_type": event_type,
        "route_name": route_name if isinstance(route_name, str) else None,
        "route_mode": route_mode if isinstance(route_mode, str) else None,
        "method": method if isinstance(method, str) else None,
        "path": path if isinstance(path, str) else None,
        "query": query if isinstance(query, str) else None,
        "status_code": status_code if isinstance(status_code, int) else None,
        "duration": duration if isinstance(duration, int | float) else None,
    }
    if event_type == "llm_responses_observed":
        trace_row["model"] = model if isinstance(model, str) else None

    with execution_context.evidence_path.open("a", encoding="utf-8") as evidence_file:
        evidence_file.write(json.dumps(trace_row) + "\n")


def emit_observation(
    *,
    execution_context: ActiveSessionDescriptor,
    request_id: str,
    observation_type: str,
    content_type: str,
    content: str,
    model: str | None,
    status_code: int | None,
) -> None:
    if not isinstance(request_id, str) or not request_id.strip():
        raise RuntimeError("request_id must be a non-empty string")
    if not isinstance(observation_type, str) or not observation_type.strip():
        raise RuntimeError("observation_type must be a non-empty string")
    if not isinstance(content_type, str) or not content_type.strip():
        raise RuntimeError("content_type must be a non-empty string")
    if not isinstance(content, str) or not content:
        raise RuntimeError("content must be a non-empty string")

    observations_path = execution_context.session_path.parent / OBSERVATIONS_FILE_NAME
    observation = {
        "case_index": execution_context.case_index,
        "request_id": request_id,
        "observation_type": observation_type,
        "content_type": content_type,
        "content": content,
        "model": model if isinstance(model, str) else None,
        "status_code": status_code if isinstance(status_code, int) else None,
        "timestamp_utc": utc_now_iso(),
    }
    with observations_path.open("a", encoding="utf-8") as observations_file:
        observations_file.write(json.dumps(observation) + "\n")
