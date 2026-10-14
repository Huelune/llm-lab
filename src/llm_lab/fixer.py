"""엔진: 묶음 하나(함께 보낼 행들)를 LLM에 보내 행마다 수정 / 수정 불필요 판단과
그 묶음이 함께 쓸 수정안을 받는다."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any

import openai
from openai import OpenAI

from llm_lab.csource import (
    MAX_DEFINITION_LINES,
    MAX_STORED_CALLERS,
    Definition,
    enclosing_function,
    pick_callers,
    pick_definitions,
    split_lines,
    top_level_blocks,
)
from llm_lab.followups import (
    AGAIN_LINE,
    ASKED_EDITABLE,
    ASKED_REFERENCE,
    LAST_LINE,
    LATER_LINE,
    MISSING_LINE,
    SHOWN_LINE,
    UNAVAILABLE_LINE,
    Asked,
    need_names,
    with_follow_ups,
    with_need,
)
from llm_lab.polyspace import Finding
from llm_lab.probe import FATAL_CATEGORIES, classify_error
from llm_lab.sources import SourceFile

if TYPE_CHECKING:
    from llm_lab.kinds import Kind

# 이 줄 수 이하면 파일 전체를 보낸다. 2,000줄은 대략 2만~3만 토큰으로, `llm-probe --context`로
# 잰 컨텍스트 길이에 넉넉히 들어간다. 더 긴 파일은 지적된 줄이 든 함수만 보낸다.
WHOLE_FILE_MAX_LINES = 2000
# 함수를 못 찾거나 함수가 너무 길면 지적된 줄 앞뒤로 이만큼 보낸다
WINDOW_LINES = 60
# 함수 시그니처를 넣으려고 '{' 위로 거슬러 올라가는 최대 줄 수
SIGNATURE_LINES = 10
# 종류가 따로 정하지 않으면 쓰는 LLM 대기 시간(초)
FIX_TIMEOUT = 120.0


class FixError(Exception):
    """묶음 하나를 처리하지 못함. 그 묶음의 행을 모두 오류로 표시한다.

    메시지는 user_error() 형식.
    """


class FatalLLMError(Exception):
    """네트워크·SSL·인증 오류. 나머지 행도 같은 이유로 실패하므로 작업 전체를 멈춘다."""


class EditMismatch(Exception):
    """LLM 답을 쓸 수 없음 (LLM에 다시 요청할 사유).

    kind는 화면에 보일 설명을 고르는 데 쓴다: locate / format / truncated / encoding / missing.
    summary를 주면 그 글을 설명으로 쓴다 (종류가 자기 검사에 맞는 설명을 붙일 때).
    """

    def __init__(self, detail: str, kind: str = "locate", summary: str = "") -> None:
        super().__init__(detail)
        self.kind = kind
        self.summary = summary or MISMATCH_SUMMARIES[kind]


# 두 번 물어도 쓸 수 없는 답이었을 때 화면에 보일 설명 (kind별)
MISMATCH_SUMMARIES = {
    "locate": "LLM이 고칠 위치를 정확히 짚지 못했습니다.",
    "format": "LLM 답을 읽을 수 없었습니다.",
    "truncated": "LLM 답이 길이 제한에 걸려 잘렸습니다.",
    "encoding": "LLM이 이 파일의 인코딩으로 쓸 수 없는 문자를 썼습니다.",
    "missing": "LLM 답에 빠진 행이 있습니다.",
}
RETRY_ACTION = "'오류 행 다시 시도'를 누르세요. 그래도 같으면 대화 기록을 보고 직접 고치세요."


def user_error(summary: str, action: str, detail: str = "") -> str:
    """화면에 보일 오류. 첫 줄은 무엇이 문제인지, 둘째 줄은 할 일, 셋째 줄은 개발자용 내용."""
    lines = [summary, f"할 일: {action}"]
    if detail:
        lines.append(f"자세히: {detail}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Edit:
    start: int  # 원본 텍스트(\n 통일) 안의 문자 위치
    end: int
    replacement: str
    # 고치는 파일. 빈 글이면 행의 파일(검사 파일), 아니면 작업 파일 이름(소스 폴더 기준 경로)
    file: str = ""


@dataclass(frozen=True)
class FixResult:
    # fix: 수정안 있음 / no_fix: 지금 코드로 괜찮음 / manual: 고쳐야 하지만 사람이 정해야 함
    decision: str  # "fix" | "no_fix" | "manual"
    reason: str
    edits: tuple[Edit, ...]  # fix가 아니면 비어 있음
    diff: str  # 화면용 unified diff, fix가 아니면 ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> FixResult:
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FixResult:
        edits = tuple(Edit(**edit) for edit in data["edits"])
        return cls(data["decision"], data["reason"], edits, data["diff"])


def select_region(text: str, line: int) -> tuple[int, int]:
    """LLM에 보낼 줄 범위 (1부터, 양 끝 포함)."""
    lines = split_lines(text)
    if len(lines) <= WHOLE_FILE_MAX_LINES:
        return 1, len(lines)
    for start, end in top_level_blocks(text):
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


def _has_numbers(text: str) -> bool:
    """줄 번호 표시('    57| ')를 복사한 줄이 하나라도 있는지."""
    return any(NUMBER_PREFIX.match(line) for line in text.split("\n"))


def _strip_numbers(text: str) -> str:
    """줄마다 앞에 붙은 줄 번호 표시를 뗀다."""
    return "\n".join(NUMBER_PREFIX.sub("", line, count=1) for line in text.split("\n"))


def _pick_by_line(candidates: list[tuple[int, int, int]], line: int | None) -> int | None:
    """후보 (시작 줄, 끝 줄, 값) 중에서 지적된 줄을 품은 것, 없으면 가장 가까운 것의 값.

    하나로 정해지지 않으면 None.
    """
    if line is None:
        return None

    def distance(candidate: tuple[int, int, int]) -> int:
        first, last, _ = candidate
        return 0 if first <= line <= last else min(abs(line - first), abs(line - last))

    best = min(distance(candidate) for candidate in candidates)
    picked = [candidate for candidate in candidates if distance(candidate) == best]
    return picked[0][2] if len(picked) == 1 else None


def _keep_ends(text: str) -> list[str]:
    """줄바꿈을 붙인 채로 \\n에서만 나눈다."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _minimize(text: str, edit: Edit) -> list[Edit]:
    """바뀌지 않은 앞뒤 줄을 덜어 내고 실제로 바뀐 줄만 남긴다 (적용 결과는 같다).

    LLM은 고칠 줄 앞뒤까지 넉넉히 묶어 보내는 일이 많다. 그대로 두면 같은 함수의 다른 행
    수정과 범위가 겹쳐 함께 적용할 수 없으므로, 실제로 바뀐 줄 단위로 줄인다.
    """
    begin = text.rfind("\n", 0, edit.start) + 1
    newline = text.find("\n", edit.end - 1 if edit.end > edit.start else edit.start)
    finish = len(text) if newline == -1 else newline + 1
    old = _keep_ends(text[begin:finish])
    new = _keep_ends(text[begin : edit.start] + edit.replacement + text[edit.end : finish])
    offsets = [begin]
    for line in old:
        offsets.append(offsets[-1] + len(line))
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [
        Edit(offsets[i1], offsets[i2], "".join(new[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def locate_edits(
    text: str,
    first: int,
    last: int,
    raw_edits: list[dict[str, str]],
    line: int | None = None,
) -> tuple[Edit, ...]:
    """LLM이 준 original을 first~last 줄 안에서 찾아 원본 위치로 바꾼다.

    같은 코드가 여러 곳에 있으면 지적된 줄(line)을 품은 곳, 없으면 가장 가까운 곳을 고른다.
    """
    starts = _line_starts(text)
    region_start, region_end = starts[first - 1], starts[min(last, len(starts) - 1)]
    region = text[region_start:region_end]
    region_lines = split_lines(region)
    edits: list[Edit] = []
    for number, raw in enumerate(raw_edits, start=1):
        original = raw["original"].replace("\r\n", "\n")
        replacement = raw["replacement"].replace("\r\n", "\n")
        # 줄 번호 표시까지 복사했으면 뗀다. 새로 쓴 줄에는 번호가 없을 수 있어 줄마다 본다.
        if _has_numbers(original):
            original, replacement = _strip_numbers(original), _strip_numbers(replacement)
        if not original.strip():
            raise EditMismatch(f"edits[{number}].original이 비어 있음")
        # original과 replacement의 끝 줄바꿈을 맞춘다 (안 맞추면 다음 줄이 붙거나 빈 줄이 생긴다)
        if replacement and original.endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"
        elif not original.endswith("\n") and replacement.endswith("\n"):
            replacement = replacement[:-1]
        found = []
        index = region.find(original)
        while index != -1:
            found.append(region_start + index)
            index = region.find(original, index + 1)
        if found:
            if len(found) == 1:
                start = found[0]
            else:
                spans = [
                    (
                        text.count("\n", 0, at) + 1,
                        text.count("\n", 0, at + len(original) - 1) + 1,
                        at,
                    )
                    for at in found
                ]
                picked = _pick_by_line(spans, line)
                if picked is None:
                    raise EditMismatch(
                        f"edits[{number}].original이 보낸 코드에 {len(found)}번 나옴"
                    )
                start = picked
            edits.append(Edit(start, start + len(original), replacement))
            continue
        # 그대로는 없으면 줄 단위로 공백 차이를 무시하고 찾는다
        wanted = [_squash(text_line) for text_line in original.strip("\n").split("\n")]
        hits = [
            index
            for index in range(len(region_lines) - len(wanted) + 1)
            if [_squash(text_line) for text_line in region_lines[index : index + len(wanted)]]
            == wanted
        ]
        if len(hits) > 1:
            spans = [(first + hit, first + hit + len(wanted) - 1, hit) for hit in hits]
            picked = _pick_by_line(spans, line)
            hits = hits if picked is None else [picked]
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
    minimal = [piece for edit in edits for piece in _minimize(text, edit)]
    return tuple(sorted(minimal, key=lambda edit: edit.start))


def apply_edits(text: str, edits: tuple[Edit, ...] | list[Edit]) -> str:
    """겹치지 않는 수정들을 뒤쪽부터 적용한다.

    같은 자리에서 시작하면 바꾸기를 먼저, 끼워 넣기를 나중에 해야 끼워 넣은 글이 바꾸기 범위에
    섞이지 않는다.
    """
    for edit in sorted(edits, key=lambda edit: (edit.start, edit.end), reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def display_diff(name: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        split_lines(old), split_lines(new), f"a/{name}", f"b/{name}", lineterm="", n=3
    )
    return "\n".join(lines)


# 공통 답 형식: 판단과 이유는 행마다, 수정(edits)은 묶음이 함께 쓴다
FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "decision": {"type": "string", "enum": ["fix", "no_fix", "manual"]},
                    "reason": {"type": "string"},
                },
                "required": ["number", "decision", "reason"],
                "additionalProperties": False,
            },
        },
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
    "required": ["findings", "edits"],
    "additionalProperties": False,
}

# 다른 파일도 고치는 종류(RTE)의 답: 수정마다 file("" = 검사 파일)을 받는다
FIX_SCHEMA_WITH_FILE = copy.deepcopy(FIX_SCHEMA)
FIX_SCHEMA_WITH_FILE["properties"]["edits"]["items"]["properties"] = {
    "file": {"type": "string"},
    "original": {"type": "string"},
    "replacement": {"type": "string"},
}
FIX_SCHEMA_WITH_FILE["properties"]["edits"]["items"]["required"] = [
    "file",
    "original",
    "replacement",
]


@dataclass(frozen=True)
class Request:
    """LLM에 보낼 요청 하나 (묶음 하나)."""

    kind: Kind
    findings: list[Finding]  # 메시지의 Finding 1, 2 … 순서
    source: SourceFile
    first: int  # 보낸 코드 범위 (1부터, 양 끝 포함)
    last: int
    context: Any  # 작업 정보 (polyspace.Prepared.data)
    messages: list[dict[str, str]]
    definitions: tuple[Definition, ...] = ()  # 코드 뒤에 붙인 다른 곳의 정의
    # 고칠 수 있게 보낸 정의·호출자 조각의 파일 (이름 → 원문). 비었으면 정의는 참고만이다
    files: Mapping[str, SourceFile] = field(default_factory=dict)
    callers: tuple[Definition, ...] = ()  # 붙인 호출자 조각 (고칠 수 있음)
    follow_ups: int = 0  # 되물을 수 있는 횟수 (0이면 되묻지 않음, 지시문·답 형식도 예전 그대로)
    asked: tuple[
        Definition, ...
    ] = ()  # 되묻기로 보낸 정의 (보낸 순서). 고칠 수 있는 범위의 마지막 순서

    @property
    def schema(self) -> Mapping[str, Any]:
        """보낼 답 형식. 되물을 수 있으면 need가 더해진다."""
        if self.follow_ups:
            return with_need(self.kind.answer_schema)
        return self.kind.answer_schema

    def with_asked(self, asked: Asked) -> Request:
        """되묻기로 보낸 정의와 새로 고칠 수 있게 된 파일을 더한 요청."""
        return replace(
            self,
            asked=(*self.asked, *asked.definitions),
            files={**self.files, **asked.files},
        )


def _shown_last(definition: Definition) -> int:
    """정의에서 보여 주는(고칠 수 있는) 마지막 줄."""
    return min(definition.last, definition.first + MAX_DEFINITION_LINES - 1)


def _definition_blocks(
    definitions: tuple[Definition, ...], editable: Mapping[str, SourceFile] | None
) -> tuple[list[str], bool]:
    """정의마다 코드 블록 하나, 그리고 고칠 수 있는 정의가 하나라도 있는지.

    editable에 원문이 있는 파일의 정의는 줄 번호를 붙여 고칠 수 있게, 나머지는 번호 없이 참고로.
    고칠 수 있는 정의가 하나도 없으면 참고 블록에 라벨을 달지 않는다 (예전과 같은 글).
    """
    editable = editable or {}
    labelled = any(definition.file in editable for definition in definitions)
    parts = []
    for definition in definitions:
        where = (
            f"line {definition.first}"
            if definition.first == definition.last
            else f"lines {definition.first}-{definition.last}"
        )
        source = editable.get(definition.file)
        if source is None:
            label = "reference only: " if labelled else ""
            parts.append(f"```c\n// {label}{definition.file} {where}\n{definition.text}\n```")
            continue
        last = _shown_last(definition)
        body = numbered(source.text, definition.first, last)
        if last < definition.last:
            body += f"\n... ({definition.last - last} more lines, not editable)"
        parts.append(f"```c\n// editable: {definition.file} {where}\n{body}\n```")
    return parts, labelled


def definitions_block(
    definitions: tuple[Definition, ...], editable: Mapping[str, SourceFile] | None = None
) -> str:
    """코드 뒤에 붙이는 다른 곳의 정의 절."""
    if not definitions:
        return ""
    parts, labelled = _definition_blocks(definitions, editable)
    if not labelled:
        heading = (
            "\n\nDefinitions from other places, for reference only. "
            "Edits must stay in the numbered code above.\n\n"
        )
    else:
        heading = (
            "\n\nDefinitions from other places. Those marked editable may be changed; "
            'set "file" to their path. Edits in the numbered code above use "file": "".\n\n'
        )
    return heading + "\n\n".join(parts)


def follow_up_message(
    asked: Asked, editable: Mapping[str, SourceFile] | None, rounds_left: int
) -> str:
    """되묻기에 답하는 사용자 메시지. rounds_left는 이 메시지 뒤에 더 되물을 수 있는 횟수."""
    parts = []
    if asked.definitions:
        blocks, labelled = _definition_blocks(asked.definitions, editable)
        heading = ASKED_EDITABLE if labelled else ASKED_REFERENCE
        parts.append(heading + "\n\n" + "\n\n".join(blocks))
    if asked.unavailable:
        parts.append(UNAVAILABLE_LINE)
    for line, names in (
        (MISSING_LINE, asked.missing),
        (SHOWN_LINE, asked.shown),
        (LATER_LINE, asked.later),
    ):
        if names:
            parts.append(line + ", ".join(names) + ".")
    if rounds_left > 0:
        times = "time" if rounds_left == 1 else "times"
        parts.append(f"{AGAIN_LINE}{rounds_left} more {times}.")
    else:
        parts.append(LAST_LINE)
    return "\n\n".join(parts)


def callers_block(
    function: str,
    callers: tuple[Definition, ...],
    hidden: int,
    files: Mapping[str, SourceFile],
    capped: bool = False,
    uses: int = 0,
) -> str:
    """검사 함수를 부르는 곳의 절. 조각마다 원래 줄 번호를 붙여 고칠 수 있게 보낸다.

    capped: 저장 상한에 닿아 저장하지 못한 호출도 있을 수 있다. uses: 이름이 호출 없이 쓰인 곳 수.
    """
    if not callers:
        return ""
    more = ""
    if hidden:
        more = f" {'At least ' if capped else ''}{hidden} more call sites are not shown."
    if uses:
        places = "place" if uses == 1 else "places"
        more += (
            f" Its name also appears without a call in {uses} {places} "
            "(possibly function pointers)."
        )
    parts = []
    for caller in callers:
        source = files[caller.file]
        name = enclosing_function(source.text, caller.first) or "?"
        where = (
            f"line {caller.first}"
            if caller.first == caller.last
            else f"lines {caller.first}-{caller.last}"
        )
        body = numbered(source.text, caller.first, caller.last)
        parts.append(f"```c\n// editable caller: {caller.file} {where} (in {name}())\n{body}\n```")
    return (
        f"\n\nCallers of {function}(), for the values that reach its parameters. "
        f'Those marked editable may be changed; set "file" to their path.{more}\n\n'
        + "\n\n".join(parts)
    )


def build_prompt(
    kind: Kind,
    findings: list[Finding],
    source: SourceFile,
    first: int,
    last: int,
    context: Any,
    definitions: tuple[Definition, ...] = (),
    editable: Mapping[str, SourceFile] | None = None,
    callers_text: str = "",
) -> str:
    blocks = []
    for number, finding in enumerate(findings, start=1):
        facts = [*kind.facts(finding), ("File", source.name), ("line", str(finding.line))]
        listed = "\n".join(f"- {name}: {value}" for name, value in facts if value)
        blocks.append(f"Finding {number}:\n{listed}")
    tail = kind.prompt_tail(context, findings, source)
    return (
        f"{kind.heading}:\n\n"
        + "\n\n".join(blocks)
        + (f"\n\n{tail}" if tail else "")
        + f"\n\nCode (lines {first}-{last} of {source.name}; "
        "each line starts with its number and '| '):\n"
        f"```c\n{numbered(source.text, first, last)}\n```"
        + definitions_block(definitions, editable)
        + callers_text
    )


def make_request(
    kind: Kind,
    findings: list[Finding],
    source: SourceFile,
    context: Any = None,
    definitions: Iterable[Definition] = (),
    files: Mapping[str, SourceFile] | None = None,
    follow_ups: int = 0,
) -> Request:
    """묶음의 줄 번호를 확인하고 보낼 메시지를 만든다. 줄 번호가 소스와 맞지 않으면 FixError.

    follow_ups: 종류가 되묻기를 쓰면 지시문에 규칙을 붙이고 답 형식에 need를 더한다.
    """
    definitions = list(definitions)
    line_count = len(split_lines(source.text))
    for finding in findings:
        line = finding.line
        if line is None:
            raise FixError(
                user_error(
                    "엑셀 line 칸에서 줄 번호를 읽지 못했습니다.",
                    "엑셀에서 이 행의 line 칸을 확인하세요. "
                    "줄 번호가 없는 지적은 직접 확인해야 합니다.",
                    f"엑셀 {finding.row}행: line 칸이 비었거나 숫자가 아님",
                )
            )
        if not 1 <= line <= line_count:
            raise FixError(
                user_error(
                    "엑셀의 줄 번호가 소스와 맞지 않습니다.",
                    "엑셀을 만든 것과 같은 버전의 소스 폴더를 골라 다시 분석하세요.",
                    f"엑셀 {finding.row}행: {line}번 줄이 없음 (파일은 {line_count}줄)",
                )
            )
    first, last = kind.region(source.text, findings)
    picked: tuple[Definition, ...] = ()
    if kind.uses_definitions:
        lines = [finding.line for finding in findings if finding.line is not None]
        picked = tuple(pick_definitions(definitions, source.name, source.text, first, last, lines))
    editable: dict[str, SourceFile] = {}
    if kind.edits_definitions and files:
        editable = {d.file: files[d.file] for d in picked if d.file in files}
    callers: tuple[Definition, ...] = ()
    callers_text = ""
    if kind.sends_callers and files:
        line = next((f.line for f in findings if f.line is not None), None)
        function = enclosing_function(source.text, line) if line is not None else None
        if function:
            found, hidden = pick_callers(definitions, function, source.name, files, first, last)
            callers = tuple(found)
            editable = {**editable, **{c.file: files[c.file] for c in callers}}
            stored = [d for d in definitions if d.kind == "caller" and d.name == function]
            home = sum(d.file == source.name for d in stored)
            capped = MAX_STORED_CALLERS <= max(home, len(stored) - home)
            uses = sum(
                int(d.text)
                for d in definitions
                if d.kind == "caller_ref" and d.name == function and d.text.isdigit()
            )
            callers_text = callers_block(function, callers, hidden, files, capped, uses)
    rounds = follow_ups if kind.asks_definitions else 0
    messages = [
        {"role": "system", "content": with_follow_ups(kind.prompt(context), rounds)},
        {
            "role": "user",
            "content": build_prompt(
                kind, findings, source, first, last, context, picked, editable, callers_text
            ),
        },
    ]
    return Request(
        kind,
        list(findings),
        source,
        first,
        last,
        context,
        messages,
        picked,
        editable,
        callers,
        follow_ups=rounds,
    )


def cache_key(model: str, request: Request) -> str:
    """보내는 메시지와 원본 파일이 같으면 같은 키. 엑셀 행 번호는 들어가지 않는다.

    결과의 수정 위치가 파일 전체 기준이라 원본 sha256도 넣는다. 지시문 글은 메시지에 있으므로,
    prompt_version은 답 처리 방식을 바꿀 때 올린다.
    """
    parts = [
        request.kind.name,
        str(request.kind.prompt_version),
        model,
        request.source.sha256,
        *(message["content"] for message in request.messages),
        *(f"{name}:{f.sha256}" for name, f in sorted(request.files.items())),
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
    schema: Any,
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
                "json_schema": {"name": "polyspace_fix", "schema": schema, "strict": True},
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
        raise FixError(
            user_error(
                "LLM 서버가 요청을 처리하지 못했습니다.",
                "잠시 뒤 '오류 행 다시 시도'를 누르세요.",
                message,
            )
        ) from exc
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


def read_decisions(data: dict[str, Any], count: int) -> list[tuple[str, str]]:
    """findings에서 번호 순서대로 (판단, 이유)를 꺼낸다. 종류별 답 처리도 이 함수를 쓴다.

    번호가 1부터 count까지 한 번씩이 아니면 EditMismatch다.
    """
    answers = data.get("findings")
    if not isinstance(answers, list) or not all(
        isinstance(item, dict)
        and type(item.get("number")) is int
        and item.get("decision") in ("fix", "no_fix", "manual")
        and isinstance(item.get("reason"), str)
        for item in answers
    ):
        raise EditMismatch("findings가 스키마와 다름", "format")
    numbers = sorted(item["number"] for item in answers)
    if numbers != list(range(1, count + 1)):
        raise EditMismatch(
            f"findings의 number는 1부터 {count}까지 한 번씩이어야 함 (받은 번호: {numbers})",
            "missing",
        )
    by_number = {item["number"]: item for item in answers}
    return [(by_number[n]["decision"], by_number[n]["reason"]) for n in range(1, count + 1)]


def share(decisions: list[tuple[str, str]], edits: tuple[Edit, ...], diff: str) -> list[FixResult]:
    """판단마다 결과 하나. fix 행만 묶음이 함께 쓰는 수정과 diff를 갖는다."""
    return [
        FixResult("fix", reason, edits, diff)
        if decision == "fix"
        else FixResult(decision, reason, (), "")
        for decision, reason in decisions
    ]


def edits_results(data: dict[str, Any], request: Request) -> list[FixResult]:
    """공통 답 처리: 행별 판단과, fix가 하나라도 있으면 edits로 만든 묶음 공통 수정.

    fix가 없으면 edits는 버린다.
    """
    decisions = read_decisions(data, len(request.findings))
    if not any(decision == "fix" for decision, _ in decisions):
        return share(decisions, (), "")
    edits, diff = _located_edits(data.get("edits"), request)
    return share(decisions, edits, diff)


def _ranges(request: Request, target: str) -> list[list[tuple[int, int]]]:
    """target 파일에서 수정을 찾을 줄 범위를 먼저 볼 순서대로 묶은 것.

    검사 파일은 보낸 범위를 먼저 보고, 거기 없을 때만 고칠 수 있게 보낸 같은 파일의 정의를 본다.
    되묻기로 보낸 정의는 그다음이다.
    """
    tiers = [[(request.first, request.last)]] if target == request.source.name else []
    if target in request.files:
        tiers.append(
            [(d.first, _shown_last(d)) for d in request.definitions if d.file == target]
            + [(c.first, c.last) for c in request.callers if c.file == target]
        )
        asked = [(d.first, _shown_last(d)) for d in request.asked if d.file == target]
        if asked:
            tiers.append(asked)
    return tiers


def _locate_one(
    text: str,
    tiers: list[list[tuple[int, int]]],
    raw: dict[str, str],
    line: int | None,
    number: int,
    name: str,
) -> tuple[Edit, ...]:
    """수정 하나를 앞선 묶음부터 찾아, 처음 찾은 묶음의 정확히 한 곳에서 고른다."""
    errors: list[EditMismatch] = []
    for ranges in tiers:
        found: list[tuple[Edit, ...]] = []
        for first, last in ranges:
            try:
                found.append(locate_edits(text, first, last, [raw], line))
            except EditMismatch as exc:
                errors.append(exc)
        unique = list(dict.fromkeys(found))
        if len(unique) == 1:
            return unique[0]
        if unique:
            raise EditMismatch(
                f"edits[{number}].original을 {name}의 보여 준 코드에서 여러 곳에 나옴"
            )
    if len(errors) == 1:
        # 범위가 하나면 찾는 쪽의 설명을 그대로 쓴다 (번호만 이 수정의 번호로)
        raise EditMismatch(str(errors[0]).replace("edits[1]", f"edits[{number}]", 1))
    raise EditMismatch(f"edits[{number}].original을 {name}의 보여 준 코드에서 찾을 수 없음")


def _loose_name(name: str) -> str:
    """LLM이 적은 파일 이름을 비교용으로 맞춘다: 역슬래시는 /로, 앞의 ./은 떼고, 대소문자는 무시."""
    name = name.replace("\\", "/")
    return (name[2:] if name.startswith("./") else name).lower()


def _located_edits(raw_edits: Any, request: Request) -> tuple[tuple[Edit, ...], str]:
    source = request.source
    if (
        not isinstance(raw_edits, list)
        or not raw_edits
        or not all(
            isinstance(edit, dict)
            and isinstance(edit.get("original"), str)
            and isinstance(edit.get("replacement"), str)
            and isinstance(edit.get("file", ""), str)
            for edit in raw_edits
        )
    ):
        raise EditMismatch("fix인데 edits가 비었거나 형식이 다름", "format")
    texts = {**request.files, source.name: source}
    names = {_loose_name(name): name for name in texts}
    by_target: dict[str, list[Edit]] = {}
    for number, raw in enumerate(raw_edits, start=1):
        asked = raw.get("file") or source.name
        target = names.get(_loose_name(asked))
        if target is None:
            raise EditMismatch(f"edits[{number}].file {asked}은 고칠 수 있게 보낸 파일이 아님")
        file = texts[target]
        # 소스 인코딩(CP949 등)으로 못 쓰는 문자가 있으면 패치를 만들 수 없으므로 지금 다시 요청한다
        try:
            raw["replacement"].encode(file.encoding)
        except UnicodeEncodeError as exc:
            char = ord(raw["replacement"][exc.start])
            raise EditMismatch(
                f"edits[{number}].replacement에 {file.encoding}로 쓸 수 없는 문자 "
                f"U+{char:04X}가 있음 - ASCII나 한글만 쓰세요",
                "encoding",
            ) from None
        line = request.findings[0].line if target == source.name else None
        located = _locate_one(file.text, _ranges(request, target), raw, line, number, target)
        by_target.setdefault(target, []).extend(located)
    edits: list[Edit] = []
    diffs: list[str] = []
    for target in [source.name, *sorted(t for t in by_target if t != source.name)]:
        # 같은 수정이 두 번 오면 한 번만 적용한다 (같은 자리의 삽입이 겹쳐 두 번 들어가지 않게)
        found = sorted(dict.fromkeys(by_target.get(target, [])), key=lambda edit: edit.start)
        if not found:
            continue
        for before, after in zip(found, found[1:], strict=False):
            if after.start < before.end:
                raise EditMismatch("edits끼리 같은 줄을 고침")
        text = texts[target].text
        fixed = apply_edits(text, found)
        if fixed != text:
            diffs.append(display_diff(target, text, fixed))
        stored = "" if target == source.name else target
        edits.extend(replace(edit, file=stored) for edit in found)
    if not diffs:
        raise EditMismatch("수정해도 원본과 같음")
    return tuple(edits), "\n".join(diffs)


def _to_results(content: str, finish_reason: str | None, request: Request) -> list[FixResult]:
    if finish_reason == "length":
        raise EditMismatch("응답이 출력 길이 제한으로 잘림", "truncated")
    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError:
        raise EditMismatch("응답이 JSON이 아님", "format") from None
    if not isinstance(data, dict):
        raise EditMismatch("응답이 JSON 객체가 아님", "format")
    results = request.kind.to_results(data, request)
    if len(results) != len(request.findings):
        raise EditMismatch("판단 수가 행 수와 다름", "format")
    return results


# 되물은 이름 → 이번에 보낼 것. worker가 프로젝트 색인으로 찾고 작업에 남긴다
Ask = Callable[[list[str], Request], Asked]


def _need_of(content: str, finish_reason: str | None) -> list[str]:
    """임시 답의 need. 잘렸거나 JSON이 아니면 EditMismatch (그 차례에서 다시 묻는다)."""
    if finish_reason == "length":
        raise EditMismatch("응답이 출력 길이 제한으로 잘림", "truncated")
    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError:
        raise EditMismatch("응답이 JSON이 아님", "format") from None
    return need_names(data)


def propose_fixes(
    client: OpenAI,
    model: str,
    request: Request,
    transcript: list[Exchange] | None = None,
    ask: Ask | None = None,
) -> list[FixResult]:
    """묶음 하나에 대한 행별 판단과 함께 쓰는 수정.

    쓸 수 없는 답이면 차례마다 한 번 다시 요청한다. ask를 주면 답의 need에 적힌 이름을 ask로
    찾아 request.follow_ups번까지 다음 메시지로 보내고 다시 판단하게 한다. 마지막 차례의 답은
    need가 있어도 최종이다. 돌려주는 목록은 request.findings와 같은 순서다.
    transcript를 주면 LLM과 주고받은 요청·응답을 순서대로 담는다.
    """
    kind = request.kind
    client = client.with_options(timeout=kind.timeout, max_retries=1)
    messages = list(request.messages)
    rounds_left = request.follow_ups if ask is not None else 0
    retry = True
    while True:
        content, finish_reason = _ask(client, model, messages, request.schema, transcript)
        try:
            need = _need_of(content, finish_reason) if rounds_left else []
            if not need:
                return _to_results(content, finish_reason, request)
        except EditMismatch as exc:
            if not retry:
                raise FixError(user_error(exc.summary, RETRY_ACTION, f"{exc} (2번 시도)")) from None
            retry = False
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your answer could not be used: {exc}. "
                    "Answer again with the same JSON format.",
                },
            ]
            continue
        assert ask is not None  # rounds_left는 ask가 있을 때만 0보다 크다
        asked = ask(need, request)
        request = request.with_asked(asked)
        rounds_left -= 1
        retry = True
        editable = request.files if kind.edits_definitions else None
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": follow_up_message(asked, editable, rounds_left)},
        ]
