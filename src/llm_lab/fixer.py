"""엔진: RTE 행 하나를 LLM에 보내 수정 / 수정 불필요 판단과 수정안을 받는다."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import openai
from openai import OpenAI

from llm_lab.polyspace import Finding
from llm_lab.probe import FATAL_CATEGORIES, classify_error
from llm_lab.sources import SourceFile

# 이 줄 수 이하면 파일 전체를 보낸다. 2,000줄은 대략 2만~3만 토큰으로, `llm-probe --context`로
# 잰 컨텍스트 길이에 넉넉히 들어간다. 더 긴 파일은 지적된 줄이 든 함수만 보낸다.
WHOLE_FILE_MAX_LINES = 2000
# 함수를 못 찾거나 함수가 너무 길면 지적된 줄 앞뒤로 이만큼 보낸다
WINDOW_LINES = 60
# 함수 시그니처를 넣으려고 '{' 위로 거슬러 올라가는 최대 줄 수
SIGNATURE_LINES = 10
FIX_TIMEOUT = 120.0
# 프롬프트나 응답 처리 방식을 바꾸면 올린다 (캐시 키에 들어간다)
PROMPT_VERSION = 2  # 2: 파일 전체를 보내는 기준 400 → 2,000줄


class FixError(Exception):
    """행 하나를 처리하지 못함. 그 행만 오류로 표시한다."""


class FatalLLMError(Exception):
    """네트워크·SSL·인증 오류. 나머지 행도 같은 이유로 실패하므로 작업 전체를 멈춘다."""


class EditMismatch(Exception):
    """LLM이 준 수정을 원본에 적용할 수 없음 (LLM에 다시 요청할 사유)."""


@dataclass(frozen=True)
class Edit:
    start: int  # 원본 텍스트(\n 통일) 안의 문자 위치
    end: int
    replacement: str


@dataclass(frozen=True)
class FixResult:
    decision: str  # "fix" | "no_fix"
    reason: str
    edits: tuple[Edit, ...]  # no_fix면 비어 있음
    diff: str  # 화면용 unified diff, no_fix면 ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> FixResult:
        data = json.loads(text)
        edits = tuple(Edit(**edit) for edit in data["edits"])
        return cls(data["decision"], data["reason"], edits, data["diff"])


def split_lines(text: str) -> list[str]:
    """\\n으로만 나눈다 (splitlines는 C 소스의 \\f 같은 문자에서도 나눠 줄 번호가 틀어진다)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _top_level_blocks(text: str) -> list[tuple[int, int]]:
    """최상위 { … } 블록의 (시작 줄, 끝 줄) 목록. 주석·문자열·문자 상수 안의 중괄호는 무시한다."""
    blocks: list[tuple[int, int]] = []
    depth = 0
    line = 1
    start = 0
    state = "code"
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\n":
            line += 1
            if state == "line_comment":
                state = "code"
        elif state == "code":
            if ch == "/" and nxt == "/":
                state, i = "line_comment", i + 1
            elif ch == "/" and nxt == "*":
                state, i = "block_comment", i + 1
            elif ch == '"':
                state = "string"
            elif ch == "'":
                state = "char"
            elif ch == "{":
                if depth == 0:
                    start = line
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0:
                    blocks.append((start, line))
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state, i = "code", i + 1
        elif state in ("string", "char"):
            if ch == "\\":
                i += 1
                if nxt == "\n":
                    line += 1
            elif (ch == '"' and state == "string") or (ch == "'" and state == "char"):
                state = "code"
        i += 1
    return blocks


def select_region(text: str, line: int) -> tuple[int, int]:
    """LLM에 보낼 줄 범위 (1부터, 양 끝 포함)."""
    lines = split_lines(text)
    if len(lines) <= WHOLE_FILE_MAX_LINES:
        return 1, len(lines)
    for start, end in _top_level_blocks(text):
        if start <= line <= end and end - start < WHOLE_FILE_MAX_LINES:
            first = start
            while first > 1 and start - first < SIGNATURE_LINES:
                above = lines[first - 2].strip()
                if not above or above.endswith((";", "}")) or above.startswith("#"):
                    break
                first -= 1
            return first, end
    return max(1, line - WINDOW_LINES), min(len(lines), line + WINDOW_LINES)


def numbered(text: str, first: int, last: int) -> str:
    lines = split_lines(text)
    return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))


def _line_starts(text: str) -> list[int]:
    """각 줄이 시작하는 문자 위치. 마지막 원소는 텍스트 끝."""
    starts = [0]
    for index, ch in enumerate(text):
        if ch == "\n":
            starts.append(index + 1)
    if starts[-1] != len(text):
        starts.append(len(text))
    return starts


def _squash(line: str) -> str:
    return " ".join(line.split())


NUMBER_PREFIX = re.compile(r"^\s*\d+\| ?")


def _strip_numbers(text: str) -> str:
    """LLM이 줄 번호 표시('    57| ')까지 복사했으면 뗀다 (내용 있는 줄이 모두 그럴 때만)."""
    lines = text.split("\n")
    body = [line for line in lines if line.strip()]
    if body and all(NUMBER_PREFIX.match(line) for line in body):
        return "\n".join(NUMBER_PREFIX.sub("", line, count=1) for line in lines)
    return text


def locate_edits(
    text: str, first: int, last: int, raw_edits: list[dict[str, str]]
) -> tuple[Edit, ...]:
    """LLM이 준 original을 first~last 줄 안에서 찾아 원본 위치로 바꾼다."""
    starts = _line_starts(text)
    region_start, region_end = starts[first - 1], starts[min(last, len(starts) - 1)]
    region = text[region_start:region_end]
    region_lines = split_lines(region)
    edits: list[Edit] = []
    for number, raw in enumerate(raw_edits, start=1):
        original = _strip_numbers(raw["original"].replace("\r\n", "\n"))
        replacement = _strip_numbers(raw["replacement"].replace("\r\n", "\n"))
        if not original.strip():
            raise EditMismatch(f"edits[{number}].original이 비어 있음")
        # original과 replacement의 끝 줄바꿈을 맞춘다 (안 맞추면 다음 줄이 붙거나 빈 줄이 생긴다)
        if replacement and original.endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"
        elif not original.endswith("\n") and replacement.endswith("\n"):
            replacement = replacement[:-1]
        count = region.count(original)
        if count == 1:
            start = region_start + region.index(original)
            edits.append(Edit(start, start + len(original), replacement))
            continue
        if count > 1:
            raise EditMismatch(f"edits[{number}].original이 보낸 코드에 {count}번 나옴")
        # 그대로는 없으면 줄 단위로 공백 차이를 무시하고 찾는다
        wanted = [_squash(line) for line in original.strip("\n").split("\n")]
        hits = [
            index
            for index in range(len(region_lines) - len(wanted) + 1)
            if [_squash(line) for line in region_lines[index : index + len(wanted)]] == wanted
        ]
        if len(hits) != 1:
            where = "찾을 수 없음" if not hits else f"{len(hits)}번 나옴"
            raise EditMismatch(f"edits[{number}].original을 보낸 코드에서 {where}")
        line_index = first - 1 + hits[0]
        start = starts[line_index]
        end = starts[line_index + len(wanted)] - 1  # 마지막 줄의 \n은 남긴다
        if text[end : end + 1] != "\n":
            end += 1  # 파일 끝 줄에 \n이 없는 경우
        edits.append(Edit(start, end, replacement.strip("\n")))
    edits.sort(key=lambda edit: edit.start)
    for before, after in zip(edits, edits[1:], strict=False):
        if after.start < before.end:
            raise EditMismatch("edits끼리 같은 줄을 고침")
    return tuple(edits)


def apply_edits(text: str, edits: tuple[Edit, ...] | list[Edit]) -> str:
    """겹치지 않는 수정들을 뒤쪽부터 적용한다."""
    for edit in sorted(edits, key=lambda edit: edit.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def display_diff(name: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        split_lines(old), split_lines(new), f"a/{name}", f"b/{name}", lineterm="", n=3
    )
    return "\n".join(lines)


SYSTEM_PROMPT = """\
You review one Polyspace Code Prover run-time check in C code \
and decide whether the code must change.
- Red Check: Polyspace proved that the operation fails whenever it runs. Fix it.
- Orange Check: Polyspace could not prove the operation safe. \
Fix it if some execution can really fail. \
If the code shown guarantees safety, answer no_fix and name the lines that guarantee it.
When you fix:
- Make the safety provable by static analysis: explicit range checks, \
division-by-zero checks, initialization and similar.
- Change as little as possible. Do not touch lines unrelated to the check. \
Keep the behavior for valid inputs.
- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
without the line-number prefix. "replacement" is the new text for those lines.
Do not guess definitions (macros, types, other functions) that are not shown. \
If they matter, say so in reason.
Write "reason" in Korean, one to three sentences that a reviewer can paste \
into a Polyspace comment.
Answer with JSON only. For no_fix, "edits" is [].
"""

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["fix", "no_fix"]},
        "reason": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["original", "replacement"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "reason", "edits"],
    "additionalProperties": False,
}


def build_prompt(finding: Finding, source: SourceFile, first: int, last: int) -> str:
    facts = [
        ("TYPE", finding.type),
        ("check", finding.check),
        ("detail", finding.detail),
        ("Group", finding.group),
        ("information", finding.information),
        ("Function", finding.function),
        ("File", source.name),
        ("line", str(finding.line)),
    ]
    listed = "\n".join(f"- {name}: {value}" for name, value in facts if value)
    return (
        f"Polyspace Code Prover result:\n{listed}\n\n"
        f"Code (lines {first}-{last} of {source.name}; "
        "each line starts with its number and '| '):\n"
        f"```c\n{numbered(source.text, first, last)}\n```"
    )


def cache_key(model: str, source: SourceFile, finding: Finding) -> str:
    parts = [
        str(PROMPT_VERSION),
        model,
        source.sha256,
        finding.type,
        finding.check,
        finding.detail,
        finding.group,
        finding.information,
        finding.function,
        str(finding.line),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Exchange:
    """대화 기록용: LLM에 보낸 메시지와 받은 응답 하나."""

    messages: list[dict[str, str]]
    raw: dict[str, Any] | None = None  # 응답 원문 (오류면 None)
    content: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    seconds: float = 0.0
    error: str = ""


def _ask(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    transcript: list[Exchange] | None,
) -> tuple[str, str | None]:
    """(응답 내용, finish_reason). 네트워크·SSL·인증 오류면 FatalLLMError."""
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "rte_fix", "schema": FIX_SCHEMA, "strict": True},
            },
        )
    except openai.APIError as exc:
        category, hint = classify_error(exc)
        message = f"[{category}] {hint}"
        if transcript is not None:
            transcript.append(
                Exchange(list(messages), seconds=time.perf_counter() - start, error=message)
            )
        if category in FATAL_CATEGORIES:
            raise FatalLLMError(message) from exc
        raise FixError(message) from exc
    choice = response.choices[0] if response.choices else None
    content = (choice.message.content if choice else None) or ""
    finish_reason = choice.finish_reason if choice else None
    if transcript is not None:
        usage = response.usage
        transcript.append(
            Exchange(
                messages=list(messages),
                raw=response.model_dump(),
                content=content,
                finish_reason=finish_reason,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                seconds=time.perf_counter() - start,
            )
        )
    return content, finish_reason


def _to_result(
    content: str, finish_reason: str | None, source: SourceFile, first: int, last: int
) -> FixResult:
    if finish_reason == "length":
        raise EditMismatch("응답이 출력 길이 제한으로 잘림")
    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError:
        raise EditMismatch("응답이 JSON이 아님") from None
    if (
        not isinstance(data, dict)
        or data.get("decision") not in ("fix", "no_fix")
        or not isinstance(data.get("reason"), str)
    ):
        raise EditMismatch("decision·reason이 스키마와 다름")
    if data["decision"] == "no_fix":
        return FixResult("no_fix", data["reason"], (), "")
    raw_edits = data.get("edits")
    if (
        not isinstance(raw_edits, list)
        or not raw_edits
        or not all(
            isinstance(edit, dict)
            and isinstance(edit.get("original"), str)
            and isinstance(edit.get("replacement"), str)
            for edit in raw_edits
        )
    ):
        raise EditMismatch("fix인데 edits가 비었거나 형식이 다름")
    for number, raw in enumerate(raw_edits, start=1):
        # 소스 인코딩(CP949 등)으로 못 쓰는 문자가 있으면 패치를 만들 수 없으므로 지금 다시 요청한다
        try:
            raw["replacement"].encode(source.encoding)
        except UnicodeEncodeError as exc:
            char = ord(raw["replacement"][exc.start])
            raise EditMismatch(
                f"edits[{number}].replacement에 {source.encoding}로 쓸 수 없는 문자 "
                f"U+{char:04X}가 있음 - ASCII나 한글만 쓰세요"
            ) from None
    edits = locate_edits(source.text, first, last, raw_edits)
    fixed = apply_edits(source.text, edits)
    if fixed == source.text:
        raise EditMismatch("수정해도 원본과 같음")
    return FixResult("fix", data["reason"], edits, display_diff(source.name, source.text, fixed))


def propose_fix(
    client: OpenAI,
    model: str,
    finding: Finding,
    source: SourceFile,
    transcript: list[Exchange] | None = None,
) -> FixResult:
    """RTE 행 하나에 대한 판단과 수정. 쓸 수 없는 응답이면 한 번만 다시 요청한다.

    transcript를 주면 LLM과 주고받은 요청·응답을 순서대로 담는다.
    """
    line_count = len(split_lines(source.text))
    if finding.line is None:
        raise FixError("line 칸이 정수가 아님")
    if not 1 <= finding.line <= line_count:
        raise FixError(
            f"{finding.line}번 줄이 없음 (파일은 {line_count}줄) - "
            "엑셀과 소스의 버전이 다를 수 있음"
        )
    first, last = select_region(source.text, finding.line)
    client = client.with_options(timeout=FIX_TIMEOUT, max_retries=1)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt(finding, source, first, last)},
    ]
    problem = ""
    for _ in range(2):
        content, finish_reason = _ask(client, model, messages, transcript)
        try:
            return _to_result(content, finish_reason, source, first, last)
        except EditMismatch as exc:
            problem = str(exc)
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your answer could not be used: {problem}. "
                    "Answer again with the same JSON format.",
                },
            ]
    raise FixError(f"LLM 응답을 쓸 수 없음 (2번 시도): {problem}")
