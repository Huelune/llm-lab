"""C 소스에서 함수 하나의 범위를 찾고, LLM이 낸 함수 코드를 기계로 확인한다 (CodeMetrics).

범위는 함수 바로 위 주석부터 닫는 '}'까지다. 도우미 함수를 그 맨 위에 끼워 넣으므로 범위가
주석이나 시그니처 한가운데서 시작하면 코드가 깨진다. 그래서 주석 줄은 글자 모양('//', '*'로
시작하는지)이 아니라 주석·문자열 상태를 따라가며 가린다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from llm_lab.fixer import SIGNATURE_LINES, _top_level_blocks, split_lines


@dataclass(frozen=True)
class FunctionRange:
    start: int  # 바꿀 범위의 첫 줄 (바로 위 주석이 있으면 그 첫 줄, 1부터)
    signature: int  # 시그니처 첫 줄
    open_line: int  # 몸통을 여는 '{' 줄
    end: int  # 닫는 '}' 줄


def _scan(text: str) -> Iterator[tuple[int, int, str, bool]]:
    """주석 밖의 글자를 (위치, 줄 번호, 글자, 문자열·문자 상수 안인지)로 내놓는다.

    주석은 빈칸 하나로 내놓는다(앞뒤 낱말이 붙지 않게). 줄바꿈은 주석 안이어도 내놓는다.
    """
    line = 1
    state = "code"
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\n":
            if state == "line_comment":
                state = "code"
            yield i, line, ch, state in ("string", "char")
            line += 1
        elif state == "code":
            if ch == "/" and nxt in ("/", "*"):
                state = "line_comment" if nxt == "/" else "block_comment"
                yield i, line, " ", False
                i += 1
            else:
                yield i, line, ch, False
                if ch == '"':
                    state = "string"
                elif ch == "'":
                    state = "char"
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 1
        elif state in ("string", "char"):
            yield i, line, ch, True
            if ch == "\\" and nxt:
                yield i + 1, line, nxt, True
                if nxt == "\n":
                    line += 1
                i += 1
            elif (ch == '"' and state == "string") or (ch == "'" and state == "char"):
                state = "code"
        i += 1


def _code_lines(text: str) -> set[int]:
    """주석 말고 다른 글자가 있는 줄 번호."""
    return {line for _, line, ch, _ in _scan(text) if not ch.isspace()}


def braces_balanced(code: str) -> bool:
    """주석·문자열·문자 상수 밖의 중괄호 짝이 맞는지."""
    depth = 0
    for _, _, ch, quoted in _scan(code):
        if quoted or ch not in "{}":
            continue
        depth += 1 if ch == "{" else -1
        if depth < 0:
            return False
    return depth == 0


def squash_code(code: str) -> str:
    """비교용: 주석을 빼고, 공백을 한 칸으로 줄이고, 기호 앞뒤 공백을 없앤다."""
    plain = " ".join("".join(ch for _, _, ch, _ in _scan(code)).split())
    return re.sub(r" ?([^\w ]) ?", r"\1", plain)


def function_name(text: str) -> str:
    """엑셀 Function 칸 → 함수 이름. 'foo()', ' foo ( void ) ' → 'foo'."""
    return text.split("(", 1)[0].strip()


def function_range(text: str, line: int, name: str) -> FunctionRange | None:
    """line에 정의된 함수 name의 범위. 못 찾으면 None.

    line은 몸통 안이거나, 몸통을 여는 '{'보다 SIGNATURE_LINES줄 안쪽 위여야 한다.
    """
    blocks = _top_level_blocks(text)
    block = next((b for b in blocks if b[0] <= line <= b[1]), None) or next(
        (b for b in blocks if line <= b[0] <= line + SIGNATURE_LINES), None
    )
    if block is None or not name:
        return None
    open_line, end = block
    lines = split_lines(text)
    code = _code_lines(text)
    signature = open_line
    while signature > 1 and open_line - signature < SIGNATURE_LINES:
        above = lines[signature - 2].strip()
        if (
            not above
            or above.endswith((";", "}"))
            or above.startswith("#")
            or signature - 1 not in code
        ):
            break
        signature -= 1
    start = signature
    while start > 1 and lines[start - 2].strip() and start - 1 not in code:
        start -= 1
    header = "\n".join(lines[signature - 1 : open_line])
    if line < start or not re.search(rf"(?<!\w){re.escape(name)}\s*\(", header):
        return None
    return FunctionRange(start, signature, open_line, end)


def signature_text(text: str, found: FunctionRange) -> str:
    """시그니처 첫 줄부터 몸통을 여는 '{' 앞까지를 squash_code로 줄인 글."""
    lines = split_lines(text)
    offset = sum(len(line) + 1 for line in lines[: found.signature - 1])
    brace = next(
        index
        for index, number, ch, quoted in _scan(text)
        if index >= offset and number == found.open_line and ch == "{" and not quoted
    )
    return squash_code(text[offset:brace])
