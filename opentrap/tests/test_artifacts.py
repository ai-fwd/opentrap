from __future__ import annotations

import json
from pathlib import Path

from opentrap.artifacts import (
    append_jsonl_artifact,
    write_csv_artifact,
    write_json_artifact,
    write_jsonl_artifact,
    write_text_artifact,
)


def test_write_json_artifact_is_atomic_utf8_newline_terminated_and_creates_parents(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "payload.json"

    write_json_artifact(path, {"message": "café", "ok": True})

    assert path.read_text(encoding="utf-8") == '{\n  "message": "café",\n  "ok": true\n}\n'
    assert not (tmp_path / "nested" / "payload.json.tmp").exists()


def test_append_jsonl_artifact_preserves_multiple_object_rows(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "events.jsonl"

    append_jsonl_artifact(path, {"event": "first"})
    append_jsonl_artifact(path, {"event": "second", "value": 2})

    assert [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] == [{"event": "first"}, {"event": "second", "value": 2}]


def test_write_jsonl_artifact_writes_batch_rows(tmp_path: Path) -> None:
    path = tmp_path / "records" / "evaluation.jsonl"

    write_jsonl_artifact(path, [{"case": 1}, {"case": 2}])

    assert path.read_text(encoding="utf-8") == '{"case": 1}\n{"case": 2}\n'


def test_write_csv_artifact_escapes_multiline_text_and_field_order(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "evaluation.csv"

    write_csv_artifact(
        path,
        [{"id": "case-1", "body": "line 1\nline 2", "score": 0.5}],
        fieldnames=["id", "score", "body"],
    )

    assert path.read_text(encoding="utf-8").splitlines()[0] == "id,score,body"
    assert '"line 1\nline 2"' in path.read_text(encoding="utf-8")


def test_write_text_artifact_creates_standalone_file(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "evaluation_report.html"

    write_text_artifact(path, "<html><body>standalone</body></html>")

    assert path.read_text(encoding="utf-8") == "<html><body>standalone</body></html>"
