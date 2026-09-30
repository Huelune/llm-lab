"""회사 LLM 연결·기능 점검 CLI."""

from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from typing import Literal

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion

SSL_ERROR = "SSL"
NETWORK_ERROR = "네트워크"
AUTH_ERROR = "인증"
PATH_ERROR = "경로"
UNSUPPORTED_ERROR = "기능 미지원"
SERVER_ERROR = "서버"
FATAL_CATEGORIES = {SSL_ERROR, NETWORK_ERROR, AUTH_ERROR}


def _has_ssl_cause(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _server_message(exc: openai.APIStatusError) -> str:
    if isinstance(exc.body, dict) and exc.body.get("message"):
        return str(exc.body["message"])
    return exc.message


def classify_error(exc: openai.APIError) -> tuple[str, str]:
    """SDK 예외를 (분류, 안내 문구)로 바꾼다."""
    if isinstance(exc, openai.APITimeoutError):
        return NETWORK_ERROR, "응답 시간 초과 - 사내망/VPN 연결과 LLM_TIMEOUT 값을 확인하세요"
    if isinstance(exc, openai.APIConnectionError):
        if _has_ssl_cause(exc):
            return SSL_ERROR, (
                "인증서 검증 실패 - Windows 인증서 저장소에 사내 루트 인증서가 있는지 확인하세요"
            )
        return NETWORK_ERROR, "서버에 접속할 수 없음 - 사내망/VPN 연결과 LLM_BASE_URL을 확인하세요"
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return AUTH_ERROR, f"인증 실패({exc.status_code}) - LLM_API_KEY를 확인하세요"
    if isinstance(exc, openai.NotFoundError):
        return PATH_ERROR, (
            "경로를 찾을 수 없음(404) - LLM_BASE_URL 끝에 /v1이 있는지, "
            "LLM_MODEL이 맞는지 확인하세요"
        )
    if isinstance(exc, openai.BadRequestError):
        return UNSUPPORTED_ERROR, (
            f"요청 거부(400) - 지원하지 않는 기능일 수 있음: {_server_message(exc)}"
        )
    if isinstance(exc, openai.APIStatusError):
        return SERVER_ERROR, f"서버 오류({exc.status_code}): {_server_message(exc)}"
    return SERVER_ERROR, str(exc)


Status = Literal["ok", "partial", "fail", "skip"]


@dataclass
class CheckResult:
    status: Status
    detail: str
    raw: object | None = None
    name: str = ""
    elapsed: float | None = None


PING_MESSAGES = [{"role": "user", "content": "Reply with exactly one word: pong"}]

BUGGY_CODE = """def average(xs):
    total = 0
    for x in xs:
        total += x
    return total / len(xs)
"""

REVIEW_PROMPT = (
    f"Review stats.py below. The code crashes on some input.\n```python\n{BUGGY_CODE}```"
)

REPORT_ISSUE_TOOL = {
    "type": "function",
    "function": {
        "name": "report_issue",
        "description": "Report one problem found in the code.",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "message": {"type": "string"},
            },
            "required": ["file", "line", "severity", "message"],
        },
    },
}
TOOL_REQUIRED_FIELDS = ("file", "line", "severity", "message")

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "line": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["file", "line", "message"],
    "additionalProperties": False,
}

TOOL_MESSAGES = [
    {
        "role": "system",
        "content": "You are a code reviewer. Report each bug by calling report_issue.",
    },
    {"role": "user", "content": REVIEW_PROMPT},
]
JSON_MESSAGES = [
    {"role": "user", "content": f"{REVIEW_PROMPT}\nAnswer in JSON with keys file, line, message."},
]


def check_models(client: OpenAI, model: str) -> CheckResult:
    try:
        page = client.models.list()
    except openai.NotFoundError:
        return CheckResult("skip", "모델 목록 API 미구현(404)")
    ids = [item.id for item in page.data]
    raw = [item.model_dump() for item in page.data]
    if model in ids:
        return CheckResult("ok", f"모델 {len(ids)}개, '{model}' 확인됨", raw)
    available = ", ".join(ids[:5]) or "없음"
    return CheckResult(
        "partial", f"목록에 '{model}' 없음 - LLM_MODEL 확인 (목록: {available})", raw
    )


def check_chat(client: OpenAI, model: str) -> CheckResult:
    response = client.chat.completions.create(model=model, messages=PING_MESSAGES)
    raw = response.model_dump()
    text = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not text:
        return CheckResult("fail", "응답은 왔지만 내용이 비어 있음", raw)
    if response.usage:
        usage = f"토큰 in {response.usage.prompt_tokens} / out {response.usage.completion_tokens}"
    else:
        usage = "토큰 정보 없음"
    return CheckResult("ok", f"응답 {text[:30]!r}, {usage}", raw)


def check_stream(client: OpenAI, model: str) -> CheckResult:
    start = time.perf_counter()
    first_token: float | None = None
    chunks = 0
    parts: list[str] = []
    stream = client.chat.completions.create(model=model, messages=PING_MESSAGES, stream=True)
    for chunk in stream:
        chunks += 1
        content = chunk.choices[0].delta.content if chunk.choices else None
        if content:
            if first_token is None:
                first_token = time.perf_counter() - start
            parts.append(content)
    if first_token is None:
        return CheckResult("fail", f"스트림은 열렸지만 내용이 없음 (청크 {chunks}개)")
    return CheckResult("ok", f"첫 토큰 {first_token:.2f}s, 청크 {chunks}개", "".join(parts))


def check_tools(client: OpenAI, model: str) -> CheckResult:
    response = client.chat.completions.create(
        model=model, messages=TOOL_MESSAGES, tools=[REPORT_ISSUE_TOOL], tool_choice="auto"
    )
    raw = response.model_dump()
    tool_calls = (response.choices[0].message.tool_calls if response.choices else None) or []
    calls = [
        call
        for call in tool_calls
        if call.type == "function" and call.function.name == "report_issue"
    ]
    if not calls:
        return CheckResult("fail", "모델이 report_issue를 호출하지 않음", raw)
    try:
        args = json.loads(calls[0].function.arguments)
    except json.JSONDecodeError:
        return CheckResult("partial", "호출은 왔지만 인자가 JSON이 아님", raw)
    if not isinstance(args, dict):
        return CheckResult("partial", "호출은 왔지만 인자가 객체가 아님", raw)
    missing = [key for key in TOOL_REQUIRED_FIELDS if key not in args]
    if missing:
        return CheckResult("partial", f"호출은 왔지만 필드 누락: {', '.join(missing)}", raw)
    return CheckResult("ok", f"report_issue(line={args['line']}) 반환", raw)


def _parse_json_object(response: ChatCompletion) -> dict | None:
    content = response.choices[0].message.content if response.choices else None
    try:
        data = json.loads(content or "")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def check_json(client: OpenAI, model: str) -> CheckResult:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=JSON_MESSAGES,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "issue", "schema": ISSUE_SCHEMA, "strict": True},
            },
        )
        data = _parse_json_object(response)
        if data is not None and all(key in data for key in ISSUE_SCHEMA["required"]):
            return CheckResult("ok", "json_schema 지원", response.model_dump())
        schema_note = "json_schema 응답이 스키마와 다름"
    except openai.BadRequestError:
        schema_note = "json_schema 미지원"

    try:
        response = client.chat.completions.create(
            model=model, messages=JSON_MESSAGES, response_format={"type": "json_object"}
        )
    except openai.BadRequestError as exc:
        return CheckResult("fail", f"{schema_note}, json_object도 미지원: {_server_message(exc)}")
    raw = response.model_dump()
    if _parse_json_object(response) is None:
        return CheckResult("fail", f"{schema_note}, json_object 응답도 JSON 객체가 아님", raw)
    return CheckResult("partial", f"{schema_note} → json_object 성공", raw)
