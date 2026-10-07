"""엔진: RTE 행 하나를 LLM에 보내 수정 / 수정 불필요 판단과 수정안을 받는다."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass

# 이 줄 수 이하면 파일 전체를 보낸다. 컨텍스트 길이를 잰 뒤 조정한다.
WHOLE_FILE_MAX_LINES = 400
# 함수를 못 찾거나 함수가 너무 길면 지적된 줄 앞뒤로 이만큼 보낸다
WINDOW_LINES = 60
# 함수 시그니처를 넣으려고 '{' 위로 거슬러 올라가는 최대 줄 수
SIGNATURE_LINES = 10


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
