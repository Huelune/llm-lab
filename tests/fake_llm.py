"""테스트용 가짜 OpenAI 호환 서버. SDK는 그대로 두고 HTTP 계층만 바꾼다."""

import json
from collections.abc import Callable

import httpx2
from openai import OpenAI

Handler = Callable[[httpx2.Request], httpx2.Response]

API_KEY = "secret-key"
MODEL = "m1"
ISSUE_ARGS = '{"file": "stats.py", "line": 5, "severity": "high", "message": "empty list"}'
ISSUE_JSON = '{"file": "stats.py", "line": 5, "message": "empty list"}'


def make_client(handler: Handler) -> OpenAI:
    return OpenAI(
        base_url="https://llm.test/v1",
        api_key=API_KEY,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def request_json(request: httpx2.Request) -> dict:
    return json.loads(request.content)


def models(*ids: str) -> httpx2.Response:
    data = [{"id": i, "object": "model", "created": 0, "owned_by": "company"} for i in ids]
    return httpx2.Response(200, json={"object": "list", "data": data})


def completion(
    content: str | None = None, *, tool_calls: list[dict] | None = None, usage: bool = True
) -> httpx2.Response:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body: dict = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    return httpx2.Response(200, json=body)


def tool_call(arguments: str, name: str = "report_issue") -> dict:
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}


def stream(*pieces: str) -> httpx2.Response:
    events = [
        {
            "id": "cmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        for piece in pieces
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=body.encode()
    )


def error(status: int, message: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"error": {"message": message, "type": "invalid_request_error"}}
    )


def request_kind(request: httpx2.Request) -> str:
    """점검 항목별 요청 구분: models / stream / tools / json / chat."""
    if request.url.path.endswith("/models"):
        return "models"
    body = request_json(request)
    if body.get("stream"):
        return "stream"
    if body.get("tools"):
        return "tools"
    if body.get("response_format"):
        return "json"
    return "chat"


def healthy_server(request: httpx2.Request) -> httpx2.Response:
    """모든 기능을 지원하는 서버."""
    kind = request_kind(request)
    if kind == "models":
        return models(MODEL)
    if kind == "stream":
        return stream("po", "ng")
    if kind == "tools":
        return completion(tool_calls=[tool_call(ISSUE_ARGS)])
    if kind == "json":
        return completion(ISSUE_JSON)
    return completion("pong")
