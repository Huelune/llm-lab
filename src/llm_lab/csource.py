"""C 소스를 가볍게 훑는 도구: 주석 지우기, 최상위 블록, 정의 찾기, 요청에 넣을 정의 고르기.

컴파일러가 아니라 중괄호 세기와 정규식으로 찾는다. 틀려도 참고용 글이 조금 비는 것뿐이다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from llm_lab.sources import DecodeError, decode_source

MAX_DEFINITION_LINES = 80  # 정의 하나를 이 줄 수에서 자른다
HEAD_LINES = 10  # 블록 위로 서명을 찾아 올라가는 최대 줄 수 (fixer.SIGNATURE_LINES와 같다)

C_KEYWORDS = frozenset(
    "auto break case char const continue default do double else enum extern float for goto "
    "if inline int long register restrict return short signed sizeof static struct switch "
    "typedef union unsigned void volatile while _Bool _Complex _Imaginary".split()
)
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
MACRO = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)")
ATTRIBUTE = re.compile(r"__attribute__\s*\(\(.*?\)\)")
TAG = re.compile(r"^\s*(?:typedef\s+)?(?:struct|union|enum)\s+([A-Za-z_]\w*)")
ENUM_HEAD = re.compile(r"^\s*(?:typedef\s+)?enum\b")
ASSIGNED = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^=]*\])?\s*=")
CALLED = re.compile(r"([A-Za-z_]\w*)\s*\(")
POINTER_NAME = re.compile(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)")
SUBSCRIPT = re.compile(r"\[[^\]]*\]")
TYPE_KEYWORD = re.compile(r"(?:typedef|struct|union|enum)\b")  # 단어 전체. enum_t는 아니다
TYPEDEF = re.compile(r"typedef\b")
EXTERN_BEFORE = re.compile(r"\bextern\s*$")  # 문자열은 지워져 `extern "C" {`가 `extern   {`가 된다
BARE_TAG = re.compile(r"(?:struct|union|enum)\s+[A-Za-z_]\w*")  # `struct tag;` 전방 선언


def split_lines(text: str) -> list[str]:
    """\\n으로만 나눈다 (splitlines는 C 소스의 \\f 같은 문자에서도 나눠 줄 번호가 틀어진다)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def blank_comments(text: str) -> str:
    """주석·문자열·문자 상수를 공백으로 바꾼 글. 줄 수와 각 줄 길이가 그대로라 줄 번호가 맞는다."""
    out: list[str] = []
    state = "code"
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            out.append("\n")
            if state == "line_comment":
                state = "code"
            i += 1
        elif state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out.append("  ")
                i += 2
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.append("  ")
                i += 2
            elif ch == '"':
                state = "string"
                out.append(" ")
                i += 1
            elif ch == "'":
                state = "char"
                out.append(" ")
                i += 1
            else:
                out.append(ch)
                i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
            else:
                out.append(" ")
                i += 1
        elif state == "line_comment":
            out.append(" ")
            i += 1
        else:  # string / char
            if ch == "\\":
                # 이스케이프 다음 글자는 건너뛴다. 줄 이음(\ 뒤 줄바꿈)은 줄 수를 지킨다
                out.append(" ")
                if nxt == "\n":
                    out.append("\n")
                elif nxt:
                    out.append(" ")
                i += 2
            else:
                if (ch == '"' and state == "string") or (ch == "'" and state == "char"):
                    state = "code"
                out.append(" ")
                i += 1
    return "".join(out)


def top_level_blocks(text: str) -> list[tuple[int, int]]:
    """최상위 { … } 블록의 (시작 줄, 끝 줄) 목록. 주석·문자열·문자 상수 안의 중괄호는 무시한다.

    `extern "C" {`의 중괄호는 블록으로 세지 않는다 (짝인 `}`는 깊이 0이라 저절로 무시된다).
    """
    blocks: list[tuple[int, int]] = []
    depth = 0
    line = 1
    start = 0
    code = blank_comments(text)
    for index, ch in enumerate(code):
        if ch == "\n":
            line += 1
        elif ch == "{":
            if depth == 0:
                if EXTERN_BEFORE.search(code[max(0, index - 200) : index]):
                    continue  # extern "C" { … }는 묶음일 뿐이다. 안의 정의는 최상위로 본다
                start = line
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                blocks.append((start, line))
    return blocks


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str  # function / macro / variable / type
    file: str  # 소스 폴더 기준 상대 경로 (/ 구분)
    first: int  # 1부터, 양 끝 포함
    last: int
    text: str  # 줄 번호 없는 원문. MAX_DEFINITION_LINES를 넘으면 잘림


def _clip(lines: list[str]) -> str:
    if len(lines) <= MAX_DEFINITION_LINES:
        return "\n".join(lines)
    rest = len(lines) - MAX_DEFINITION_LINES
    return "\n".join([*lines[:MAX_DEFINITION_LINES], f"... ({rest} more lines)"])


def _head_start(code: list[str], start: int) -> int:
    """블록 위로 서명(또는 선언)이 시작하는 줄. select_region과 같은 규칙, 주석은 비어 있다."""
    first = start
    while first > 1 and start - first < HEAD_LINES:
        above = code[first - 2].strip()
        if not above or above.endswith((";", "}")) or above.startswith("#"):
            break
        first -= 1
    return first


def _statement_end(code: list[str], end: int) -> int:
    """블록 끝 줄부터 3줄 안에서 ';'가 있는 줄. 없으면 블록 끝 줄."""
    for number in range(end, min(end + 2, len(code)) + 1):
        if ";" in code[number - 1]:
            return number
    return end


def _names_after_brace(code: list[str], end: int, last: int) -> list[str]:
    """`} name, *other;`에서 이름들."""
    tail = " ".join(code[end - 1 : last]).rsplit("}", 1)[-1].split(";", 1)[0]
    return [name for name in IDENTIFIER.findall(ATTRIBUTE.sub(" ", tail)) if name not in C_KEYWORDS]


def _enumerators(code: list[str], start: int, end: int) -> list[str]:
    """enum 블록의 열거 상수 이름. 쉼표로 나눈 조각마다 첫 식별자다."""
    body = " ".join(code[start - 1 : end])
    body = body.split("{", 1)[-1].rsplit("}", 1)[0]
    found = []
    for piece in body.split(","):
        match = IDENTIFIER.search(piece)
        if match:
            found.append(match.group(0))
    return found


def find_definitions(file: str, text: str) -> list[Definition]:
    """파일 하나의 함수·매크로·전역 변수·타입 정의. 같은 이름이 같은 줄에서 시작하면 하나만."""
    lines = split_lines(text)
    code = split_lines(blank_comments(text))
    found: list[Definition] = []
    seen: set[tuple[str, int]] = set()

    def add(name: str, kind: str, first: int, last: int) -> None:
        if name in C_KEYWORDS or (name, first) in seen:
            return
        seen.add((name, first))
        found.append(Definition(name, kind, file, first, last, _clip(lines[first - 1 : last])))

    covered: set[int] = set()  # 정의로 읽은 줄. 블록 밖 문장 찾기에서 건너뛴다

    # 매크로: 줄 끝 '\'가 이어지는 동안 다음 줄까지
    number = 1
    while number <= len(lines):
        match = MACRO.match(code[number - 1])
        if match:
            last = number
            while last < len(lines) and lines[last - 1].rstrip().endswith("\\"):
                last += 1
            add(match.group(1), "macro", number, last)
            covered.update(range(number, last + 1))
            number = last + 1
        else:
            number += 1

    # 블록: 함수, 초기화 블록이 있는 전역, struct·union·enum
    for start, end in top_level_blocks(text):
        first = _head_start(code, start)
        head = ATTRIBUTE.sub(" ", " ".join(code[first - 1 : start]).split("{", 1)[0]).strip()
        last = end
        # 타입 정의는 키워드 꼴: 키워드로 시작하고 `{` 앞에 '('나 '='가 없다.
        # `struct point make(void)`는 함수, `struct cfg g = {…}`는 전역이다
        if TYPE_KEYWORD.match(head) and "(" not in head and "=" not in head:
            last = _statement_end(code, end)
            tag = TAG.match(head)
            if tag:
                add(tag.group(1), "type", first, last)
            if TYPEDEF.match(head):
                for name in _names_after_brace(code, end, last):
                    add(name, "type", first, last)
            if ENUM_HEAD.match(head):
                for name in _enumerators(code, start, end):
                    add(name, "type", first, last)
        elif "=" in head:
            last = _statement_end(code, end)
            match = ASSIGNED.search(head)
            if match:
                add(match.group(1), "variable", first, last)
        elif "(" in head:
            match = CALLED.search(head)
            if match:
                add(match.group(1), "function", first, end)
        covered.update(range(first, last + 1))

    # 블록 밖의 문장: 초기화 없는 전역, extern 선언, 한 줄 typedef
    pending: list[int] = []
    for number, line in enumerate(code, start=1):
        if number in covered:
            pending = []
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pending.append(number)
        if ";" in line:
            _add_statement(add, code, pending)
            pending = []
    return found


def _add_statement(add, code: list[str], numbers: list[int]) -> None:
    text = " ".join(code[number - 1] for number in numbers).split(";", 1)[0].strip()
    text = ATTRIBUTE.sub(" ", text)
    first, last = numbers[0], numbers[-1]
    if TYPEDEF.match(text):
        flat = SUBSCRIPT.sub("", text)
        pointer = POINTER_NAME.search(flat)
        if pointer:
            add(pointer.group(1), "type", first, last)
            return
        words = [name for name in IDENTIFIER.findall(flat) if name not in C_KEYWORDS]
        if words:
            add(words[-1], "type", first, last)
        return
    declared = text.split("=", 1)[0].strip()  # '(' 검사는 '=' 앞에서만. 값의 괄호는 상관없다
    if "(" in declared or BARE_TAG.fullmatch(declared):
        return  # 함수 원형, 전방 선언
    flat = SUBSCRIPT.sub("", declared)
    words = [name for name in IDENTIFIER.findall(flat) if name not in C_KEYWORDS]
    if words:
        add(words[-1], "variable", first, last)


SOURCE_SUFFIXES = {".c", ".h"}


def scan_folder(root: Path) -> tuple[list[Definition], int]:
    """root 아래 모든 .c·.h의 정의와 못 읽은 파일 수. 점으로 시작하는 폴더는 건너뛴다."""
    definitions: list[Definition] = []
    unreadable = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if Path(name).suffix.casefold() not in SOURCE_SUFFIXES:
                continue
            path = Path(folder, name)
            relative = path.relative_to(root).as_posix()
            try:
                source = decode_source(relative, path.read_bytes())
            except DecodeError, OSError:
                unreadable += 1
                continue
            definitions.extend(find_definitions(relative, source.text))
    return definitions, unreadable


def _names(code: str) -> list[str]:
    """주석을 지운 글의 식별자, 처음 나온 순서로 한 번씩. C 예약어는 뺀다."""
    found: list[str] = []
    known: set[str] = set()
    for name in IDENTIFIER.findall(code):
        if name not in known and name not in C_KEYWORDS:
            known.add(name)
            found.append(name)
    return found


def identifiers(text: str) -> list[str]:
    return _names(blank_comments(text))


def used_definitions(definitions: Iterable[Definition], texts: Iterable[str]) -> list[Definition]:
    """분석 대상 파일들이 쓰는 이름의 정의만, 이름·파일·줄 순서로."""
    used: set[str] = set()
    for text in texts:
        used.update(identifiers(text))
    return sorted(
        (d for d in definitions if d.name in used), key=lambda d: (d.name, d.file, d.first)
    )


MAX_DEFINITIONS = 12  # 요청 하나에 붙이는 정의 수
MAX_DEFINITION_TOTAL_LINES = 400  # 요청 하나에 붙이는 정의 줄 수 합계
NEAR_LINES = 2  # 검사 줄 위아래로 이만큼 안의 이름을 먼저 고른다
PER_NAME = 2  # 같은 이름의 정의는 요청 하나에 이만큼


def _rank(definition: Definition, source_name: str) -> int:
    if definition.file == source_name:
        return 0
    return 1 if definition.file.casefold().endswith(".h") else 2


def pick_definitions(
    definitions: Iterable[Definition],
    source_name: str,
    text: str,
    first: int,
    last: int,
    lines: Iterable[int],
) -> list[Definition]:
    """보내는 범위(first~last)가 쓰는 이름의 정의 가운데 범위 밖의 것을, 상한 안에서 고른다.

    검사 줄(lines) 위아래 NEAR_LINES 안의 이름을 행 순서대로 먼저, 나머지는 범위 안 첫 등장 순서로.
    같은 이름은 같은 파일 → 헤더 → 그 밖 순서로 PER_NAME개까지. 같은 몸체(파일·시작 줄)는 한 번만.
    같은 입력에는 같은 결과.
    """
    code = split_lines(blank_comments(text))[first - 1 : last]
    near: list[str] = []
    for line in lines:
        lo, hi = max(line - NEAR_LINES, first), min(line + NEAR_LINES, last)
        near.extend(_names("\n".join(code[lo - first : hi - first + 1])))
    wanted = _names(" ".join(near)) if near else []
    for name in _names("\n".join(code)):
        if name not in wanted:
            wanted.append(name)

    by_name: dict[str, list[Definition]] = {}
    for definition in definitions:
        if definition.file == source_name and first <= definition.first and definition.last <= last:
            continue  # 이미 보내는 코드 안에 있다
        by_name.setdefault(definition.name, []).append(definition)

    picked: list[Definition] = []
    placed: set[tuple[str, int]] = set()  # 열거 상수·별칭마다 같은 몸체가 따로 있다. 한 번만 싣는다
    total = 0
    for name in wanted:
        candidates = sorted(
            by_name.get(name, ()), key=lambda d: (_rank(d, source_name), d.file, d.first)
        )
        for definition in candidates[:PER_NAME]:
            if (definition.file, definition.first) in placed:
                continue
            size = definition.text.count("\n") + 1
            if len(picked) >= MAX_DEFINITIONS or total + size > MAX_DEFINITION_TOTAL_LINES:
                return picked
            picked.append(definition)
            placed.add((definition.file, definition.first))
            total += size
    return picked
