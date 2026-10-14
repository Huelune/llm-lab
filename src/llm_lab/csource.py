"""C 소스를 가볍게 훑는 도구: 주석 지우기, 최상위 블록, 정의 찾기, 요청에 넣을 정의 고르기.

컴파일러가 아니라 중괄호 세기와 정규식으로 찾는다. 틀려도 참고용 글이 조금 비는 것뿐이다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Container, Iterable, Mapping
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


def top_level_blocks(text: str, code: str | None = None) -> list[tuple[int, int]]:
    """최상위 { … } 블록의 (시작 줄, 끝 줄) 목록. 주석·문자열·문자 상수 안의 중괄호는 무시한다.

    `extern "C" {`의 중괄호는 블록으로 세지 않는다 (짝인 `}`는 깊이 0이라 저절로 무시된다).
    code에 이미 주석을 지운 글을 주면 다시 지우지 않는다.
    """
    blocks: list[tuple[int, int]] = []
    depth = 0
    line = 1
    start = 0
    if code is None:
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


def _function_name(head: str) -> str | None:
    """서명 머리글에서 함수 이름. `FUNC(void, CODE) name(void)`처럼 앞에 매크로가 붙을 수 있어
    괄호 밖에서 마지막으로 `이름(`이 나오는 것을 쓴다. 괄호 밖에 없으면(`int (*get(int))(void)`)
    마지막 것. 매개변수의 함수 포인터 형(`cb_t (*cb)(int)`)에 속지 않는다."""
    calls = [m for m in CALLED.finditer(head) if m.group(1) not in C_KEYWORDS]
    outer = [m for m in calls if head[: m.start()].count("(") == head[: m.start()].count(")")]
    chosen = outer or calls
    return chosen[-1].group(1) if chosen else None


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
            name = _function_name(head)
            if name:
                add(name, "function", first, end)
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
    declared = text.split("=", 1)[0].strip()  # ')' 검사는 '=' 앞에서만. 값의 괄호는 상관없다
    if declared.endswith(")"):
        # 함수 원형(끝이 ')'). VAR(type, class) name은 변수다.
        # 함수 포인터 변수는 이름을 못 읽어 뺀다
        name = _function_name(declared)
        if name:
            add(name, "declaration", first, last)
        return
    if BARE_TAG.fullmatch(declared):
        return  # 전방 선언
    flat = SUBSCRIPT.sub("", declared)
    words = [name for name in IDENTIFIER.findall(flat) if name not in C_KEYWORDS]
    if words:
        add(words[-1], "variable", first, last)


def function_blocks(text: str, code: list[str] | None = None) -> list[tuple[str, int, int]]:
    """파일의 함수 정의: (이름, 서명 첫 줄, 닫는 중괄호 줄). find_definitions와 같은 규칙.

    code에 주석을 지운 줄 목록(split_lines(blank_comments(text)))을 주면 다시 지우지 않는다.
    """
    if code is None:
        code = split_lines(blank_comments(text))
    found: list[tuple[str, int, int]] = []
    for start, end in top_level_blocks(text, "\n".join(code)):
        first = _head_start(code, start)
        head = ATTRIBUTE.sub(" ", " ".join(code[first - 1 : start]).split("{", 1)[0]).strip()
        if TYPE_KEYWORD.match(head) and "(" not in head and "=" not in head:
            continue
        if "=" in head or "(" not in head:
            continue
        name = _function_name(head)
        if name:
            found.append((name, first, end))
    return found


def enclosing_function(text: str, line: int) -> str | None:
    """그 줄을 품은 함수의 이름 (서명 줄 포함). 함수 밖이면 None."""
    for name, first, end in function_blocks(text):
        if first <= line <= end:
            return name
    return None


def find_call_sites(
    file: str, text: str, names: Mapping[str, str], code: list[str] | None = None
) -> list[Definition]:
    """names의 이름을 부르는 줄마다 위아래 CALLER_CONTEXT 줄을 그 함수 범위 안에서 잘라 조각으로.

    조각의 name은 부른 이름이 가리키는 검사 함수다. 검사 함수 자신 안의 호출(재귀)은 뺀다.
    한 함수 안에서 겹치거나 붙은 창은 합치되 MAX_DEFINITION_LINES를 넘지 않게 나눈다.
    code는 function_blocks와 같다.
    """
    lines = split_lines(text)
    if code is None:
        code = split_lines(blank_comments(text))
    sites: list[Definition] = []
    for caller, first, end in function_blocks(text, code):
        windows: dict[str, list[list[int]]] = {}
        for number in range(first, end + 1):
            for match in CALLED.finditer(code[number - 1]):
                target = names.get(match.group(1))
                if target is None or target == caller:
                    continue
                lo = max(first, number - CALLER_CONTEXT)
                hi = min(end, number + CALLER_CONTEXT)
                spans = windows.setdefault(target, [])
                if (
                    spans
                    and lo <= spans[-1][1] + 1
                    and max(hi, spans[-1][1]) - spans[-1][0] < MAX_DEFINITION_LINES
                ):
                    spans[-1][1] = max(spans[-1][1], hi)
                elif spans and lo <= spans[-1][1]:
                    spans.append([spans[-1][1] + 1, hi])
                else:
                    spans.append([lo, hi])
        for target, spans in windows.items():
            for lo, hi in spans:
                text_part = "\n".join(lines[lo - 1 : hi])
                sites.append(Definition(target, "caller", file, lo, hi, text_part))
    return sites


SOURCE_SUFFIXES = {".c", ".h"}


def scan_project(root: Path) -> tuple[list[Definition], int, dict[str, str]]:
    """root 아래 모든 .c·.h의 정의, 못 읽은 파일 수, 파일 이름 → 원문. 점 폴더는 건너뛴다."""
    definitions: list[Definition] = []
    texts: dict[str, str] = {}
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
            texts[relative] = source.text
            definitions.extend(find_definitions(relative, source.text))
    return definitions, unreadable, texts


def scan_folder(root: Path) -> tuple[list[Definition], int]:
    """root 아래 모든 .c·.h의 정의와 못 읽은 파일 수."""
    definitions, unreadable, _ = scan_project(root)
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
    """분석 대상 파일들이 쓰는 이름의 정의만, 이름·파일·줄 순서로.

    매크로가 다른 이름의 별칭이면(`#define A B`) 그 이름의 정의도 ALIAS_DEPTH 단계까지 넣는다.
    """
    definitions = list(definitions)
    used: set[str] = set()
    for text in texts:
        used.update(identifiers(text))
    frontier = set(used)
    for _ in range(ALIAS_DEPTH):
        targets = {
            target
            for d in definitions
            if d.name in frontier and (target := macro_target(d)) and target not in used
        }
        if not targets:
            break
        used |= targets
        frontier = targets
    return sorted(
        (d for d in definitions if d.name in used), key=lambda d: (d.name, d.file, d.first)
    )


ALIAS_DEPTH = 3  # 매크로 별칭을 따라가는 최대 단계
# `#define NAME` 또는 `#define NAME(params)` 뒤의 본문
MACRO_BODY = re.compile(r"^\s*#\s*define\s+[A-Za-z_]\w*(?:\([^)]*\))?\s*(.*)$", re.S)
# 본문이 다른 이름 하나이거나 그 이름의 호출로 시작하면 별칭이다: B, B(x), (B(x)), ((B))
ALIAS_BODY = re.compile(r"^\(*\s*([A-Za-z_]\w*)\s*(?:\(|\)*\s*$)")


def macro_target(definition: Definition) -> str | None:
    """별칭 매크로가 가리키는 이름. 별칭이 아니거나 매크로가 아니면 None."""
    if definition.kind != "macro":
        return None
    text = definition.text.replace("\\\n", " ")
    body = MACRO_BODY.match(text)
    if not body:
        return None
    alias = ALIAS_BODY.match(body.group(1).strip())
    if not alias:
        return None
    target = alias.group(1)
    if target in C_KEYWORDS or target == definition.name:
        return None
    return target


CALLER_CONTEXT = 10  # 호출 줄 위아래로 이만큼을 호출자 조각으로 보낸다
MAX_CALLERS = 4  # 요청 하나에 붙이는 호출자 조각 수
MAX_CALLER_LINES = 120  # 요청 하나에 붙이는 호출자 조각 줄 수 합계
MAX_STORED_CALLERS = 20  # 검사 함수 하나에 저장하는 호출자 조각 수
# 요청 하나에 붙이는 정의 수와 줄 수 합계. RTE처럼 매크로·타입이 많은 코드도 필요한 함수 구현까지
# 닿도록 넉넉히 둔다(1500줄은 대략 2만 토큰으로, 회사 모델의 컨텍스트에 여유가 크다)
MAX_DEFINITIONS = 40
MAX_DEFINITION_TOTAL_LINES = 1500
NEAR_LINES = 2  # 검사 줄 위아래로 이만큼 안의 이름을 먼저 고른다
PER_NAME = 2  # 같은 이름의 정의는 요청 하나에 이만큼


STATIC = re.compile(r"\bstatic\b")
HEAD_END = re.compile(r"[{(]")


def _is_static(definition: Definition) -> bool:
    """함수 정의의 서명 머리(첫 `{`·`(` 앞)에 static이 있는지."""
    return bool(STATIC.search(HEAD_END.split(blank_comments(definition.text), maxsplit=1)[0]))


def _uses_without_call(names: Iterable[str]) -> re.Pattern[str]:
    """이름 뒤에 `(`가 없는 쓰임(`&f`, 표의 `f,`). 멤버(`.f`, `->f`)는 뺀다."""
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w.])(?<!->)({alternatives})(?!\w)(?!\s*\()")


def callers_for(
    definitions: Iterable[Definition], texts: Mapping[str, str], targets: Mapping[str, str]
) -> list[Definition]:
    """검사 함수(targets: 이름 → 그 함수가 든 파일)를 부르는 조각. 별칭 매크로를 통한 호출도 센다.

    함수마다 같은 파일의 조각 MAX_STORED_CALLERS개와 다른 파일의 조각 MAX_STORED_CALLERS개까지,
    같은 파일 먼저, 그다음 파일 이름·시작 줄 순서로. static 함수이거나 같은 이름의 함수가 여러
    파일에 정의돼 있으면 이름으로는 다른 파일의 호출을 가릴 수 없어 같은 파일의 조각만 둔다.
    이름이 호출 없이 쓰인 곳(함수 포인터일 수 있다)이 있으면 그 수를
    Definition(이름, "caller_ref", "", 0, 0, 수)로 하나 더한다. 이름이 없는 파일은 읽지 않는다.
    """
    if not targets:
        return []
    names = {name: name for name in targets}
    defined_in: dict[str, set[str]] = {}
    local: set[str] = set()  # 같은 파일 안에서만 찾는 함수
    for definition in definitions:
        target = macro_target(definition)
        if target in targets and definition.name not in names:
            names[definition.name] = target
        if definition.kind == "function" and definition.name in targets:
            defined_in.setdefault(definition.name, set()).add(definition.file)
            if definition.file == targets[definition.name] and _is_static(definition):
                local.add(definition.name)
    local |= {name for name, files in defined_in.items() if len(files) > 1}

    def wanted(target: str, file: str) -> bool:
        return target not in local or file == targets[target]

    uses = _uses_without_call(names)
    sites: list[Definition] = []
    refs: dict[str, int] = {}
    for file, text in sorted(texts.items()):
        if not any(name in text for name in names):
            continue
        code = split_lines(blank_comments(text))
        sites.extend(
            site for site in find_call_sites(file, text, names, code) if wanted(site.name, file)
        )
        for line in code:
            if line.lstrip().startswith("#"):
                continue  # 별칭 매크로의 본문은 호출 없는 쓰임으로 세지 않는다
            for match in uses.finditer(line):
                target = names[match.group(1)]
                if wanted(target, file):
                    refs[target] = refs.get(target, 0) + 1
    kept: list[Definition] = []
    for target, home in sorted(targets.items()):
        mine = sorted(
            (site for site in sites if site.name == target), key=lambda d: (d.file, d.first)
        )
        kept.extend([site for site in mine if site.file == home][:MAX_STORED_CALLERS])
        kept.extend([site for site in mine if site.file != home][:MAX_STORED_CALLERS])
        if refs.get(target):
            kept.append(Definition(target, "caller_ref", "", 0, 0, str(refs[target])))
    return kept


def pick_callers(
    definitions: Iterable[Definition],
    function: str,
    source_name: str,
    available: Container[str] | None = None,
    first: int | None = None,
    last: int | None = None,
) -> tuple[list[Definition], int]:
    """function을 부르는 조각을 요청 상한 안에서 고른다: (고른 것, 고르지 못한 수).

    available을 주면 그 파일의 조각만 본다(원문이 작업에 있는 파일). first~last(보내는 범위)를
    주면 source_name 파일에서 그 안에 든 조각은 이미 보이므로 고르지도 세지도 않는다.
    """

    def shown(d: Definition) -> bool:
        if first is None or last is None:
            return False
        return d.file == source_name and first <= d.first and d.last <= last

    mine = sorted(
        (
            d
            for d in definitions
            if d.kind == "caller"
            and d.name == function
            and (available is None or d.file in available)
            and not shown(d)
        ),
        key=lambda d: (d.file != source_name, d.file, d.first),
    )
    picked: list[Definition] = []
    total = 0
    for definition in mine:
        size = definition.last - definition.first + 1
        if len(picked) >= MAX_CALLERS or total + size > MAX_CALLER_LINES:
            break
        picked.append(definition)
        total += size
    return picked, len(mine) - len(picked)


def definition_rank(definition: Definition, source_name: str) -> int:
    """정의 후보 순서: 검사 파일 0, 헤더 1, 그 밖 2."""
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
    declarations: bool = False,
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
        if hi < lo:
            continue  # 검사 줄이 범위에서 멀다
        near.extend(_names("\n".join(code[lo - first : hi - first + 1])))
    wanted = _names(" ".join(near)) if near else []
    for name in _names("\n".join(code)):
        if name not in wanted:
            wanted.append(name)

    by_name: dict[str, list[Definition]] = {}
    for definition in definitions:
        if definition.kind == "declaration" and not declarations:
            continue  # 함수 원형은 쓰는 종류(MISRA)만 붙인다
        if definition.kind in ("caller", "caller_ref"):
            continue  # 호출자 조각과 그 수는 pick_callers·make_request가 따로 쓴다
        if definition.file == source_name and first <= definition.first and definition.last <= last:
            continue  # 이미 보내는 코드 안에 있다
        by_name.setdefault(definition.name, []).append(definition)

    picked: list[Definition] = []
    placed: set[tuple[str, int]] = set()  # 열거 상수·별칭마다 같은 몸체가 따로 있다. 한 번만 싣는다
    seen: set[str] = set()  # 이미 고른 이름 (별칭 순환을 막는다)
    total = 0

    def place(name: str, depth: int) -> bool:
        """name의 정의를 싣고, 별칭이면 가리키는 구현을 바로 뒤에 싣는다. 상한에 닿으면 False."""
        nonlocal total
        if name in seen:
            return True
        seen.add(name)
        candidates = sorted(
            by_name.get(name, ()), key=lambda d: (definition_rank(d, source_name), d.file, d.first)
        )
        for definition in candidates[:PER_NAME]:
            if (definition.file, definition.first) in placed:
                continue
            size = definition.text.count("\n") + 1
            if len(picked) >= MAX_DEFINITIONS or total + size > MAX_DEFINITION_TOTAL_LINES:
                return False
            picked.append(definition)
            placed.add((definition.file, definition.first))
            total += size
            target = macro_target(definition)
            if target and depth < ALIAS_DEPTH and not place(target, depth + 1):
                return False
        return True

    def is_code(name: str) -> bool:
        """함수 구현이거나 다른 이름을 가리키는 별칭 매크로인지 (구현까지 따라간다)."""
        return any(d.kind == "function" or macro_target(d) for d in by_name.get(name, ()))

    # 함수 구현(과 그 별칭)을 먼저, 타입·상수 매크로·변수는 그 뒤에. 각각 검사 줄 근처 순서를 지킨다
    ordered = [name for name in wanted if is_code(name)]
    ordered += [name for name in wanted if not is_code(name)]
    for name in ordered:
        if not place(name, 0):
            break
    return picked
