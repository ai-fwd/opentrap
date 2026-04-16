"""Runtime session state machine used by the adapter process during trap runs.

This module exposes session lifecycle and evidence APIs consumed by adapters so run
manifests, evidence logs, and reports are emitted in a consistent contract shape.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentrap.io_utils import load_json, utc_now_iso, write_json


@dataclass(frozen=True)
class DataItem:
    id: str
    path: str


@dataclass(frozen=True)
class FinalizeResult:
    run_id: str
    session_id: str
    report_path: str


@dataclass
class _ActiveSession:
    manifest_path: Path
    run_dir: Path
    run_id: str
    session_id: str
    session_path: Path
    evidence_path: Path
    data_items: dict[str, DataItem]
    started_at_utc: str
    event_count: int = 0
    events_by_type: Counter[str] = field(default_factory=Counter)


_active_session: _ActiveSession | None = None


def _require_active_session() -> _ActiveSession:
    """Return active session state or fail when adapter forgot to start one."""
    if _active_session is None:
        raise RuntimeError("no active session; call start_session(manifest_path) first")
    return _active_session


def _load_data_items_from_manifest(manifest: dict[str, Any]) -> dict[str, DataItem]:
    """Load item-id to item-path mapping from trap entries in the run manifest."""
    traps = manifest.get("traps", [])
    if not isinstance(traps, list):
        raise RuntimeError("manifest.traps must be a list")

    items: dict[str, DataItem] = {}
    for trap_entry in traps:
        if not isinstance(trap_entry, dict):
            continue
        trap_items = trap_entry.get("data_items", [])
        if not isinstance(trap_items, list):
            continue
        for trap_item in trap_items:
            if not isinstance(trap_item, dict):
                continue
            item_id = trap_item.get("id")
            path = trap_item.get("path")
            if not isinstance(item_id, str) or not isinstance(path, str):
                continue
            items[item_id] = DataItem(id=item_id, path=path)
    return items


def start_session(manifest_path: str | Path) -> str:
    """Start a runtime session and mark the run manifest as session-active."""
    global _active_session

    if _active_session is not None:
        raise RuntimeError("an active session already exists in this process")

    manifest_file = Path(manifest_path)
    if not manifest_file.exists():
        raise RuntimeError(f"manifest was not found at {manifest_file}")

    manifest = load_json(manifest_file)
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

    if manifest.get("active_session_id"):
        raise RuntimeError("manifest already has an active session")

    run_dir = manifest_file.parent
    session_id = uuid.uuid4().hex
    started_at_utc = utc_now_iso()
    session_path = run_dir / f"session-{session_id}.json"
    evidence_path = run_dir / f"session-{session_id}.jsonl"

    session_payload = {
        "run_id": run_id,
        "session_id": session_id,
        "started_at_utc": started_at_utc,
        "ended_at_utc": None,
        "event_count": 0,
    }
    write_json(session_path, session_payload, atomic=True)
    evidence_path.write_text("", encoding="utf-8")

    sessions = manifest.get("sessions")
    if not isinstance(sessions, list):
        sessions = []
    sessions.append(
        {
            "session_id": session_id,
            "started_at_utc": started_at_utc,
            "ended_at_utc": None,
        }
    )
    manifest["sessions"] = sessions
    manifest["active_session_id"] = session_id
    manifest["status"] = "session_active"
    write_json(manifest_file, manifest, atomic=True)

    _active_session = _ActiveSession(
        manifest_path=manifest_file,
        run_dir=run_dir,
        run_id=run_id,
        session_id=session_id,
        session_path=session_path,
        evidence_path=evidence_path,
        data_items=_load_data_items_from_manifest(manifest),
        started_at_utc=started_at_utc,
    )
    return session_id


def list_data_items() -> list[DataItem]:
    """Return all data items registered for the active session."""
    session = _require_active_session()
    return list(session.data_items.values())


def get_data_item(item_id: str) -> DataItem:
    """Return a single data item by id from the active session."""
    session = _require_active_session()
    try:
        return session.data_items[item_id]
    except KeyError as exc:
        raise KeyError(f"unknown data item id: {item_id}") from exc


def emit_event(event_type: str, payload: Mapping[str, Any]) -> None:
    """Append one evidence event to the active session evidence stream."""
    session = _require_active_session()
    if not isinstance(event_type, str) or not event_type.strip():
        raise RuntimeError("event_type must be a non-empty string")
    payload_object = dict(payload)

    envelope = {
        "event_type": event_type,
        "timestamp_utc": utc_now_iso(),
        "run_id": session.run_id,
        "session_id": session.session_id,
        "payload": payload_object,
    }
    with session.evidence_path.open("a", encoding="utf-8") as evidence_file:
        evidence_file.write(json.dumps(envelope) + "\n")

    session.event_count += 1
    session.events_by_type[event_type] += 1


def end_session() -> FinalizeResult:
    """Finalize the active session and emit run-level report metadata."""
    global _active_session

    session = _require_active_session()
    ended_at_utc = utc_now_iso()

    session_payload = load_json(session.session_path)
    session_payload["ended_at_utc"] = ended_at_utc
    session_payload["event_count"] = session.event_count
    write_json(session.session_path, session_payload, atomic=True)

    manifest = load_json(session.manifest_path)
    sessions = manifest.get("sessions", [])
    if isinstance(sessions, list):
        for item in sessions:
            if not isinstance(item, dict):
                continue
            if item.get("session_id") == session.session_id:
                item["ended_at_utc"] = ended_at_utc
                break
    manifest["sessions"] = sessions
    manifest["active_session_id"] = None
    manifest["status"] = "finalized"
    manifest["finalized_at_utc"] = ended_at_utc
    manifest["scorer_status"] = "pending"

    traps = manifest.get("traps", [])
    trap_ids = [
        trap_entry["trap_id"]
        for trap_entry in traps
        if isinstance(trap_entry, dict) and isinstance(trap_entry.get("trap_id"), str)
    ]
    report_path = session.run_dir / "report.json"
    report_payload = {
        "run_id": session.run_id,
        "session_id": session.session_id,
        "started_at_utc": session.started_at_utc,
        "ended_at_utc": ended_at_utc,
        "scorer_status": "pending",
        "trap_count": len(trap_ids),
        "trap_ids": trap_ids,
        "data_item_count": len(session.data_items),
        "event_count": session.event_count,
        "events_by_type": dict(session.events_by_type),
    }
    write_json(report_path, report_payload, atomic=True)

    manifest["report_path"] = str(report_path)
    write_json(session.manifest_path, manifest, atomic=True)

    _active_session = None
    return FinalizeResult(
        run_id=session.run_id,
        session_id=session.session_id,
        report_path=str(report_path),
    )
