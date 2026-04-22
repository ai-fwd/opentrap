# OpenTrap adapter default handler tests.
# Verifies standardized OpenAI Responses observe helper payload extraction.
from __future__ import annotations

import asyncio

from fastapi import Response

from opentrap.adapter.default_handlers import observe_openai_responses_default


class _FakeRequest:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakeContext:
    def __init__(self, *, request_id: str, request_body: bytes) -> None:
        self.request_id = request_id
        self.request = _FakeRequest(request_body)


def test_observe_openai_responses_default_extracts_model_and_output() -> None:
    ctx = _FakeContext(
        request_id="req-1",
        request_body=b'{"model":"gpt-test","input":["a","b"]}',
    )
    snapshot = Response(
        content=b'{"id":"resp_123","output":[{"type":"output_text","text":"hello"}]}',
        status_code=200,
        headers={"content-type": "application/json"},
    )

    payload = asyncio.run(observe_openai_responses_default(ctx, snapshot))

    assert payload == {
        "model": "gpt-test",
        "output": [{"type": "output_text", "text": "hello"}],
    }
