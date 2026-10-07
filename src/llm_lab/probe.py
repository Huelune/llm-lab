"""회사 LLM 연결·기능 점검 CLI."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Literal, TextIO

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion

from llm_lab.client import get_client
from llm_lab.config import ConfigError, Settings, load_settings

SSL_ERROR = "SSL"
NETWORK_ERROR = "네트워크"
AUTH_ERROR = "인증"
PATH_ERROR = "경로"
UNSUPPORTED_ERROR = "기능 미지원"
SERVER_ERROR = "서버"
FATAL_CATEGORIES = {SSL_ERROR, NETWORK_ERROR, AUTH_ERROR}


def _brief(text: str, limit: int = 200) -> str:
    """공백·줄바꿈을 한 칸으로 줄이고 limit자에서 자른다 (HTML 오류 페이지가 화면을 덮지 않게)."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _cause_chain(exc: BaseException) -> list[BaseException]:
    """exc에서 __cause__/__context__를 따라간 예외 목록. 순환이 있어도 멈춘다."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _root_cause_note(chain: list[BaseException]) -> str:
    root = chain[-1]
    message = _brief(str(root))
    return f" (원인: {type(root).__name__}{': ' + message if message else ''})"


def _server_message(exc: openai.APIStatusError) -> str:
    if isinstance(exc.body, dict) and exc.body.get("message"):
        return _brief(str(exc.body["message"]))
    return _brief(exc.message)


def classify_error(exc: openai.APIError) -> tuple[str, str]:
    """SDK 예외를 (분류, 안내 문구)로 바꾼다."""
    if isinstance(exc, openai.APITimeoutError):
        cause = _root_cause_note(_cause_chain(exc))
        return NETWORK_ERROR, (
            f"응답 시간 초과 - 사내망/VPN 연결과 LLM_TIMEOUT 값을 확인하세요{cause}"
        )
    if isinstance(exc, openai.APIConnectionError):
        chain = _cause_chain(exc)
        cause = _root_cause_note(chain)
        if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
            return SSL_ERROR, (
                "인증서 검증 실패 - OS 인증서 저장소에 사내 루트 인증서가 있는지 확인하세요. "
                f"사설 인증서 서버라면 .env에 LLM_VERIFY_SSL=false{cause}"
            )
        if any(isinstance(item, ssl.SSLError) for item in chain):
            return SSL_ERROR, (
                f"TLS 연결 실패 - LLM_BASE_URL의 http/https 여부와 프록시를 확인하세요{cause}"
            )
        return NETWORK_ERROR, (
            f"서버에 접속할 수 없음 - 사내망/VPN 연결과 LLM_BASE_URL을 확인하세요{cause}"
        )
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return AUTH_ERROR, (
            f"인증 실패({exc.status_code}) - LLM_API_KEY를 확인하세요"
            f" - 서버 메시지: {_server_message(exc)}"
        )
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
    # arguments가 문자열이 아닌 응답은 pydantic이 직렬화 경고를 내므로 끈다
    raw = response.model_dump(warnings=False)
    tool_calls = (response.choices[0].message.tool_calls if response.choices else None) or []
    calls = [
        call
        for call in tool_calls
        if call.type == "function" and call.function.name == "report_issue"
    ]
    if not calls:
        return CheckResult("fail", "모델이 report_issue를 호출하지 않음", raw)
    arguments = calls[0].function.arguments
    if not isinstance(arguments, str):
        # 일부 게이트웨이는 문자열 대신 객체·null을 돌려준다 (SDK는 그대로 통과시킨다)
        return CheckResult(
            "partial", f"호출은 왔지만 인자가 문자열이 아님({type(arguments).__name__})", raw
        )
    try:
        args = json.loads(arguments)
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


# 아이콘만 쓰면 cp949로 리다이렉트될 때 모두 '?'가 되므로 한글 라벨을 함께 붙인다
LABELS: dict[Status, str] = {
    "ok": "✅ 지원",
    "partial": "⚠️ 부분",
    "fail": "❌ 실패",
    "skip": "⏭ 건너뜀",
}

# 200이지만 OpenAI 형식이 아닌 응답(프록시·SSO 로그인 페이지 등)의 분류
FORMAT_ERROR = "응답 형식"


class ProbeAborted(Exception):
    """네트워크·SSL·인증 오류. 이후 점검도 같은 이유로 실패하므로 중단한다."""

    def __init__(self, result: CheckResult, category: str) -> None:
        super().__init__(result.detail)
        self.result = result
        self.category = category


Check = Callable[[OpenAI, str], CheckResult]

CHAT_CHECK_NAME = "기본 채팅"
CHECKS: list[tuple[str, Check]] = [
    ("연결·모델 목록", check_models),
    (CHAT_CHECK_NAME, check_chat),
    ("스트리밍", check_stream),
    ("Tool calling", check_tools),
    ("JSON 출력", check_json),
]


def run_check(name: str, check: Check, client: OpenAI, model: str) -> CheckResult:
    """점검 하나를 실행한다. 네트워크·SSL·인증 오류면 ProbeAborted를 던진다."""
    start = time.perf_counter()
    fatal: tuple[str, openai.APIError] | None = None
    try:
        result = check(client, model)
    except openai.APIError as exc:
        category, hint = classify_error(exc)
        result = CheckResult("fail", f"[{category}] {hint}")
        if category in FATAL_CATEGORIES:
            fatal = (category, exc)
    except Exception as exc:
        # SDK가 HTML 같은 본문을 파싱하지 못하면 AttributeError·TypeError 등으로 나온다
        result = CheckResult(
            "fail",
            f"[{FORMAT_ERROR}] 응답을 처리하지 못함 - 프록시·로그인 페이지일 수 있음 "
            f"({type(exc).__name__}: {_brief(str(exc))})",
        )
    result.name = name
    result.elapsed = time.perf_counter() - start
    if fatal:
        category, exc = fatal
        raise ProbeAborted(result, category) from exc
    return result


def format_result(index: int, result: CheckResult) -> str:
    elapsed = f"  {result.elapsed:.2f}s" if result.elapsed is not None else ""
    return f"[{index}] {result.name:<14} {LABELS[result.status]}  {result.detail}{elapsed}"


def exit_code(results: list[CheckResult]) -> int:
    """기본 채팅이 성공했으면 0, 아니면 1 (중단된 경우에도 같은 규칙)."""
    chat_ok = any(r.name == CHAT_CHECK_NAME and r.status == "ok" for r in results)
    return 0 if chat_ok else 1


def run_probe(
    client: OpenAI, settings: Settings, *, verbose: bool = False, output: TextIO | None = None
) -> int:
    """모든 점검을 실행해 결과를 출력하고 종료 코드를 반환한다 (기본 채팅 성공 시 0).

    output을 주면 화면과 같은 내용을 그 파일에도 쓴다.
    """

    def emit(text: str) -> None:
        # 서버가 오류 메시지에 키를 되돌려 보내도 출력에는 남기지 않는다
        if settings.api_key:
            text = text.replace(settings.api_key, "***")
        print(text)
        if output is not None:
            print(text, file=output)

    # 결과 파일만 보고도 어떤 설정으로 점검했는지 알 수 있게 기본값이 아닌 옵션을 적는다
    options = [f"model: {settings.model}"]
    if not settings.api_key:
        options.append("API 키 없음")
    if not settings.verify_ssl:
        options.append("인증서 검사 끔")
    if settings.enable_thinking is not None:
        options.append(f"enable_thinking={str(settings.enable_thinking).lower()}")
    emit(f"회사 LLM 점검: {settings.base_url} ({', '.join(options)})\n")
    results: list[CheckResult] = []
    for index, (name, check) in enumerate(CHECKS, start=1):
        try:
            result = run_check(name, check, client, settings.model)
        except ProbeAborted as aborted:
            emit(format_result(index, aborted.result))
            emit(f"\n점검 중단: {aborted.category} 문제는 이후 항목도 같은 이유로 실패합니다.")
            return exit_code(results)
        results.append(result)
        emit(format_result(index, result))
        if verbose and result.raw is not None:
            emit(json.dumps(result.raw, ensure_ascii=False, indent=2, default=str))

    counts = {status: sum(r.status == status for r in results) for status in LABELS}
    emit(
        f"\n요약: 지원 {counts['ok']} / 부분 지원 {counts['partial']} / "
        f"미지원 {counts['fail']} / 건너뜀 {counts['skip']}"
    )
    return exit_code(results)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # 파이프·파일로 리다이렉트된 cp949 콘솔에서도 인코딩 오류로 죽지 않게 한다
        stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="llm-probe", description="회사 LLM 연결·기능 점검")
    parser.add_argument("--verbose", action="store_true", help="각 응답의 원문 JSON도 출력")
    parser.add_argument("--output", metavar="FILE", help="화면 출력을 UTF-8 파일로도 저장")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1
    client = get_client(settings).with_options(max_retries=0)
    # Windows에는 tee가 없고 셸 리다이렉트는 셸마다 인코딩이 달라서 파일은 직접 쓴다
    with open(args.output, "w", encoding="utf-8") if args.output else nullcontext() as output:
        return run_probe(client, settings, verbose=args.verbose, output=output)
