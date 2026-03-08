import pytest

from app.llm import _stream_openrouter_chat_completion


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, lines: list[str]):
        self._response = _FakeStreamResponse(lines)

    def stream(self, *_args, **_kwargs):
        return self._response


@pytest.mark.anyio
async def test_stream_chat_completion_returns_on_terminal_finish_reason_without_done():
    client = _FakeClient([
        'data: {"id":"gen-1","choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"id":"gen-1","choices":[{"delta":{},"finish_reason":"stop"}]}',
    ])

    result = await _stream_openrouter_chat_completion(
        client,
        {"model": "google/gemini-2.5-flash", "messages": []},
    )

    assert result["id"] == "gen-1"
    assert result["choices"][0]["message"]["content"] == "Hello"
    assert result["choices"][0]["finish_reason"] == "stop"
