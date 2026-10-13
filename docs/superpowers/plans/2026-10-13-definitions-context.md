# 다른 파일의 정의를 LLM에 함께 보내기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 묶음을 LLM에 보낼 때, 보내는 코드가 쓰는 함수·매크로·전역 변수·타입의 정의를 소스 폴더의 다른 파일에서 찾아 참고용으로 붙인다.

**Architecture:** 새 모듈 `csource.py`가 C 소스를 가볍게 훑어(주석·문자열을 지운 글에서 중괄호 세기와 정규식) 정의를 찾고 요청마다 고른다. 작업을 만들 때 소스 폴더 전체를 한 번 훑어 분석 대상 파일이 쓰는 이름의 정의만 DB(스키마 4)에 저장하고, 작업자는 `make_request`에 그 목록을 넘겨 코드 범위 밖의 정의를 사용자 메시지 뒤에 붙인다. RTE·MISRA 지시문이 정의의 쓰임과 한계를 설명한다.

**Tech Stack:** Python 3.14, uv, pytest, ruff, SQLite(표준 라이브러리). 외부 의존성 추가 없음.

**Spec:** `docs/superpowers/specs/2026-10-13-definitions-context-design.md`

## Global Constraints

- 코드·테스트·문서·커밋에 회사 값(파일·함수·매크로 이름, 시트 이름, 개수, 임계값)을 쓰지 않는다. 테스트 이름은 모두 지어낸다.
- 정의 하나는 `MAX_DEFINITION_LINES = 80`줄까지, 요청 하나에 `MAX_DEFINITIONS = 12`개, 합쳐서 `MAX_DEFINITION_TOTAL_LINES = 400`줄까지.
- 같은 이름의 정의는 요청 하나에 최대 2개. 순서는 같은 파일 → 헤더(`.h`) → 그 밖.
- 정의 블록에는 줄 번호 표시를 붙이지 않는다. 수정의 `original`은 번호 붙은 코드에서만 베낀다.
- `prompt_version`은 올리지 않는다(답 처리 방식이 같다). 지시문 글이 바뀌므로 저장된 답은 자동으로 갈린다.
- `csource`는 `fixer`를 가져오지 않는다(순환 방지). `fixer`가 `csource`에서 가져온다.
- 저장소 규칙: ruff format·ruff check·pytest 전부 통과 뒤 커밋. 커밋 메시지는 영어, 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 모든 명령은 저장소 루트에서 `uv run …`으로 실행한다.

## Review Focus

- 같은 이름이 여러 파일에 정의된 경우(예: 파일마다 있는 `static` 보조 함수): 같은 파일 → 헤더 → 그 밖 순서로 최대 2개만 보내야 한다. (Task 4 테스트)
- 수천 줄짜리 초기화 표(`const … table[] = { … };`): 80줄에서 잘리고 `... (N more lines)`가 붙어야 한다. (Task 2 테스트)
- `#ifdef`로 같은 매크로가 한 헤더에 두 번 정의된 경우: 둘 다 저장되고 둘 다 보내진다. (Task 4 테스트)
- 검사 줄이 보내는 범위의 끝에서 2줄 안에 있을 때: 위아래 2줄 창이 범위 밖으로 나가지 않아야 한다. (Task 4 테스트)
- 소스 폴더에 못 읽는 파일(줄바꿈이 섞임, 다른 인코딩)과 대문자 확장자 `.H`가 섞인 경우: 못 읽는 파일은 세기만 하고 `.H`는 읽는다. (Task 3 테스트)

---

### Task 1: `csource.py`로 블록 스캐너 옮기기

**Files:**
- Create: `src/llm_lab/csource.py`
- Modify: `src/llm_lab/fixer.py` (`split_lines`, `_top_level_blocks` 정의를 지우고 가져오기)
- Test: `tests/test_csource.py`

**Interfaces:**
- Produces: `csource.split_lines(text) -> list[str]`, `csource.blank_comments(text) -> str`, `csource.top_level_blocks(text) -> list[tuple[int, int]]`. `fixer.split_lines`와 `fixer.select_region`은 이름 그대로 남는다(테스트가 `fixer`에서 가져온다).

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_csource.py`를 만든다.

```python
from llm_lab.csource import blank_comments, split_lines, top_level_blocks

CODE = """\
/* { 주석 속 중괄호 */
int add(int a, int b) // {
{
    char c = '{';
    const char *s = "}";
    return a + b;
}
static int table[2] = {1, 2};
"""


def test_blank_comments_keeps_line_count_and_column_positions():
    blanked = blank_comments(CODE)

    assert blanked.count("\n") == CODE.count("\n")
    assert [len(line) for line in split_lines(blanked)] == [len(line) for line in split_lines(CODE)]
    assert "주석" not in blanked and "'{'" not in blanked and '"}"' not in blanked
    assert "int add(int a, int b)" in blanked


def test_blank_comments_keeps_newline_escaped_inside_string():
    text = 'const char *s = "a\\\nb";\nint x;\n'

    assert blank_comments(text).count("\n") == text.count("\n")


def test_top_level_blocks_ignores_braces_in_comments_strings_and_chars():
    assert top_level_blocks(CODE) == [(3, 7), (8, 8)]


def test_split_lines_drops_only_the_trailing_empty_piece():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("a\n\n") == ["a", ""]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: `ModuleNotFoundError: No module named 'llm_lab.csource'`

- [ ] **Step 3: 모듈 만들기**

`src/llm_lab/csource.py`:

```python
"""C 소스를 가볍게 훑는 도구: 주석 지우기, 최상위 블록, 정의 찾기, 요청에 넣을 정의 고르기.

컴파일러가 아니라 중괄호 세기와 정규식으로 찾는다. 틀려도 참고용 글이 조금 비는 것뿐이다.
"""

from __future__ import annotations


def split_lines(text: str) -> list[str]:
    """\\n으로만 나눈다 (splitlines는 C 소스의 \\f 같은 문자에서도 나눠 줄 번호가 틀어진다)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def blank_comments(text: str) -> str:
    """주석·문자열·문자 상수를 공백으로 바꾼 글. 줄 수와 각 줄의 길이가 그대로라 줄 번호가 맞는다."""
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
    """최상위 { … } 블록의 (시작 줄, 끝 줄) 목록. 주석·문자열·문자 상수 안의 중괄호는 무시한다."""
    blocks: list[tuple[int, int]] = []
    depth = 0
    line = 1
    start = 0
    for ch in blank_comments(text):
        if ch == "\n":
            line += 1
        elif ch == "{":
            if depth == 0:
                start = line
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                blocks.append((start, line))
    return blocks
```

`src/llm_lab/fixer.py`에서:
- `from llm_lab.sources import SourceFile` 위에 `from llm_lab.csource import split_lines, top_level_blocks`를 더한다(ruff가 import 순서를 맞춘다). `split_lines`는 이 모듈의 다른 코드가 그대로 쓰고, 테스트는 `fixer.split_lines`로 가져오므로 그 이름이 남는다.
- `def split_lines(...)` 정의(4줄)와 `def _top_level_blocks(...)` 정의(상태 기계 전체)를 지운다.
- `select_region` 안의 `_top_level_blocks(text)`를 `top_level_blocks(text)`로 바꾼다. 그 밖에 `_top_level_blocks`를 부르는 곳이 있으면 같이 바꾼다 (`grep -n "_top_level_blocks" src tests`로 확인).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_csource.py tests/test_fixer_edits.py tests/test_kinds.py`
Expected: 모두 PASS (옮긴 뒤에도 `select_region` 동작이 같다)

- [ ] **Step 5: 전체 확인과 커밋**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: 모두 통과

```bash
git add src/llm_lab/csource.py src/llm_lab/fixer.py tests/test_csource.py
git commit -m "refactor: move the C block scanner to csource for reuse"
```

---

### Task 2: 파일 하나에서 정의 찾기

**Files:**
- Modify: `src/llm_lab/csource.py`
- Test: `tests/test_csource.py`

**Interfaces:**
- Produces: `Definition(name, kind, file, first, last, text)` frozen dataclass(`kind`는 `"function" | "macro" | "variable" | "type"`), `find_definitions(file: str, text: str) -> list[Definition]`, 상수 `MAX_DEFINITION_LINES = 80`, `C_KEYWORDS`.

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_csource.py`에 더한다 (import 줄에 `MAX_DEFINITION_LINES, Definition, find_definitions`를 더한다).

```python
HEADER = """\
#ifndef LIMITS_H
#define LIMITS_H
#define BUF_LEN 16
#define MIN(a, b) ((a) < (b) ? \\
    (a) : (b))
typedef unsigned char u8;
typedef int pair_t[2];
typedef void (*handler_t)(int);
typedef struct point_tag {
    int x;
    int y;
} point_t, *point_p;
enum { SLOT_A, SLOT_B = 4, SLOT_COUNT };
struct bare_tag { int z; };
extern u8 shared_buf[BUF_LEN];
int read_value(int index);
#endif
"""

SOURCE = """\
#include "limits.h"
/* 전역 표 */
static const u8 lookup[3] = {
    1, 2, 3
};
int counter = 0;
__attribute__((noinline)) static int helper(int a,
                                             int b)
{
    return a + b; /* { */
}
char *name_of(int id)
{
    return "x";
}
"""


def names(definitions: list[Definition]) -> list[tuple[str, str, int, int]]:
    return [(d.name, d.kind, d.first, d.last) for d in definitions]


def test_find_definitions_in_a_header():
    found = names(find_definitions("inc/limits.h", HEADER))

    assert ("LIMITS_H", "macro", 2, 2) in found
    assert ("BUF_LEN", "macro", 3, 3) in found
    assert ("MIN", "macro", 4, 5) in found  # 줄 이음은 다음 줄까지
    assert ("u8", "type", 6, 6) in found
    assert ("pair_t", "type", 7, 7) in found
    assert ("handler_t", "type", 8, 8) in found
    assert ("point_tag", "type", 9, 12) in found
    assert ("point_t", "type", 9, 12) in found and ("point_p", "type", 9, 12) in found
    assert ("SLOT_A", "type", 13, 13) in found and ("SLOT_COUNT", "type", 13, 13) in found
    assert ("bare_tag", "type", 14, 14) in found
    assert ("shared_buf", "variable", 15, 15) in found
    assert not any(name == "read_value" for name, _, _, _ in found)  # 원형은 정의가 아니다


def test_find_definitions_in_a_source_file():
    found = names(find_definitions("src/a.c", SOURCE))

    assert ("lookup", "variable", 3, 5) in found
    assert ("counter", "variable", 6, 6) in found
    assert ("helper", "function", 7, 11) in found  # 서명이 두 줄, 속성은 무시
    assert ("name_of", "function", 12, 15) in found
    assert not any(name in ("include", "a", "b") for name, _, _, _ in found)


def test_definition_text_is_the_original_lines_without_numbers():
    (lookup,) = [d for d in find_definitions("src/a.c", SOURCE) if d.name == "lookup"]

    assert lookup.file == "src/a.c"
    assert lookup.text == "static const u8 lookup[3] = {\n    1, 2, 3\n};"


def test_long_definition_is_clipped():
    body = "\n".join(f"    {i}," for i in range(200))
    text = f"const int big[] = {{\n{body}\n}};\n"

    (big,) = find_definitions("t.c", text)

    lines = big.text.split("\n")
    assert len(lines) == MAX_DEFINITION_LINES + 1
    assert lines[-1] == f"... ({202 - MAX_DEFINITION_LINES} more lines)"  # 머리 1줄 + 200줄 + '};'
    assert (big.first, big.last) == (1, 202)


def test_same_name_at_the_same_place_is_recorded_once():
    text = "typedef struct node { int v; } node;\n"

    assert names(find_definitions("n.h", text)) == [("node", "type", 1, 1)]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: `ImportError: cannot import name 'Definition'`

- [ ] **Step 3: 구현**

`src/llm_lab/csource.py`의 import 아래에 더한다.

```python
import re
from dataclasses import dataclass

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
TYPE_HEADS = ("typedef", "struct", "union", "enum")


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
        if head.startswith(TYPE_HEADS):
            last = _statement_end(code, end)
            tag = TAG.match(head)
            if tag:
                add(tag.group(1), "type", first, last)
            if head.startswith("typedef"):
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
    if text.startswith("typedef"):
        flat = SUBSCRIPT.sub("", text)
        pointer = POINTER_NAME.search(flat)
        if pointer:
            add(pointer.group(1), "type", first, last)
            return
        words = [name for name in IDENTIFIER.findall(flat) if name not in C_KEYWORDS]
        if words:
            add(words[-1], "type", first, last)
        return
    if "(" in text or text.startswith(("struct", "union", "enum")):
        return  # 함수 원형, 전방 선언
    flat = SUBSCRIPT.sub("", text).split("=", 1)[0]
    words = [name for name in IDENTIFIER.findall(flat) if name not in C_KEYWORDS]
    if words:
        add(words[-1], "variable", first, last)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: PASS. 틀리는 항목이 있으면 그 규칙(정규식)만 고친다. 테스트 기대값은 설계 §3의 규칙이다.

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/csource.py tests/test_csource.py
git commit -m "feat: find function, macro, variable and type definitions in a C file"
```

---

### Task 3: 폴더 훑기와 쓰는 이름으로 거르기

**Files:**
- Modify: `src/llm_lab/csource.py`
- Test: `tests/test_csource.py`

**Interfaces:**
- Consumes: `sources.decode_source`, `sources.DecodeError`
- Produces: `scan_folder(root: Path) -> tuple[list[Definition], int]` (정의 목록, 못 읽은 파일 수), `identifiers(text: str) -> list[str]` (처음 나온 순서, 중복·예약어 없음), `used_definitions(definitions, texts: Iterable[str]) -> list[Definition]` (이름·파일·줄 순 정렬)

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_csource.py`에 더한다 (import에 `identifiers, scan_folder, used_definitions`, 그리고 `from pathlib import Path`).

```python
def test_scan_folder_reads_c_and_h_in_any_case_and_counts_unreadable(tmp_path):
    (tmp_path / "inc").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "inc" / "LIMITS.H").write_bytes(b"#define BUF_LEN 16\n")
    (tmp_path / "main.c").write_bytes("int counter; /* 주석 */\n".encode("cp949"))
    (tmp_path / "mixed.c").write_bytes(b"int a;\r\nint b;\n")  # 줄바꿈이 섞여 못 읽음
    (tmp_path / "notes.txt").write_bytes(b"int ignored;\n")
    (tmp_path / ".git" / "hook.c").write_bytes(b"int hidden;\n")

    definitions, unreadable = scan_folder(tmp_path)

    # 훑는 순서는 폴더 구조를 따르므로 정렬해서 비교한다
    assert sorted((d.name, d.file) for d in definitions) == [
        ("BUF_LEN", "inc/LIMITS.H"),
        ("counter", "main.c"),
    ]
    assert unreadable == 1


def test_identifiers_are_unique_in_order_without_keywords_or_comment_text():
    text = "int total = BUF_LEN + lookup[0]; // BUF_LEN again, hidden_name\nreturn total;\n"

    assert identifiers(text) == ["total", "BUF_LEN", "lookup"]


def test_used_definitions_keeps_only_names_the_analysed_sources_use():
    definitions = [
        Definition("BUF_LEN", "macro", "inc/limits.h", 1, 1, "#define BUF_LEN 16"),
        Definition("unused_fn", "function", "b.c", 1, 3, "int unused_fn(void)\n{\n}"),
        Definition("counter", "variable", "a.c", 6, 6, "int counter = 0;"),
    ]

    kept = used_definitions(definitions, ["int x = BUF_LEN;\n", "counter++;\n"])

    assert [d.name for d in kept] == ["BUF_LEN", "counter"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: `ImportError: cannot import name 'scan_folder'`

- [ ] **Step 3: 구현**

`src/llm_lab/csource.py`에 더한다 (import에 `import os`, `from collections.abc import Iterable`, `from pathlib import Path`, `from llm_lab.sources import DecodeError, decode_source`).

```python
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
            except (DecodeError, OSError):
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
    return sorted((d for d in definitions if d.name in used), key=lambda d: (d.name, d.file, d.first))
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/csource.py tests/test_csource.py
git commit -m "feat: scan a source folder for definitions and keep the names a job uses"
```

---

### Task 4: 요청마다 정의 고르기

**Files:**
- Modify: `src/llm_lab/csource.py`
- Test: `tests/test_csource.py`

**Interfaces:**
- Produces: `pick_definitions(definitions, source_name: str, text: str, first: int, last: int, lines: Iterable[int]) -> list[Definition]`, 상수 `MAX_DEFINITIONS = 12`, `MAX_DEFINITION_TOTAL_LINES = 400`, `NEAR_LINES = 2`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_csource.py`에 더한다 (import에 `MAX_DEFINITIONS, MAX_DEFINITION_TOTAL_LINES, pick_definitions`).

```python
def definition(name, file="inc/limits.h", first=1, kind="macro", lines=1):
    text = "\n".join(f"{name} line {i}" for i in range(lines))
    return Definition(name, kind, file, first, first + lines - 1, text)


REGION = """\
#include "limits.h"
static int local_helper(void)
{
    return 1;
}
int use(int i)
{
    int v = lookup[i % BUF_LEN];
    int w = v;
    int x = w;
    int y = x;
    return y + local_helper() + counter;
}
"""


def test_pick_prefers_names_near_the_finding_then_the_rest_in_order():
    definitions = [
        definition("counter", file="a.c", kind="variable"),
        definition("BUF_LEN"),
        definition("lookup", file="a.c", kind="variable"),
    ]

    # 검사 줄 12의 위아래 2줄(10~13)에는 counter만 있고, lookup·BUF_LEN은 8줄에 있다
    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [12])

    assert [d.name for d in picked] == ["counter", "lookup", "BUF_LEN"]


def test_pick_skips_definitions_inside_the_code_being_sent():
    inside = Definition("local_helper", "function", "src/use.c", 2, 5, "...")
    outside = Definition("local_helper", "function", "other.c", 2, 5, "...")

    picked = pick_definitions([inside, outside], "src/use.c", REGION, 1, 13, [12])

    assert picked == [outside]


def test_pick_ranks_same_file_then_headers_and_keeps_two_per_name():
    definitions = [
        definition("BUF_LEN", file="z.c"),
        definition("BUF_LEN", file="inc/limits.h"),
        definition("BUF_LEN", file="src/use.c", first=90),
        definition("BUF_LEN", file="inc/other.h"),
    ]

    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [8])

    assert [(d.file, d.first) for d in picked] == [("src/use.c", 90), ("inc/limits.h", 1)]


def test_pick_keeps_both_ifdef_variants_from_one_header():
    definitions = [definition("BUF_LEN", first=3), definition("BUF_LEN", first=7)]

    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [8])

    assert [d.first for d in picked] == [3, 7]


def test_pick_window_does_not_leave_the_sent_range():
    definitions = [definition("counter", file="a.c", kind="variable")]

    # 검사 줄 12의 창(10~14)이 범위 끝 13을 넘고, 검사 줄 8의 창(6~10)이 범위 시작 8 앞으로 나간다
    assert [d.name for d in pick_definitions(definitions, "src/use.c", REGION, 8, 13, [12])] == ["counter"]
    assert [d.name for d in pick_definitions(definitions, "src/use.c", REGION, 8, 13, [8])] == ["counter"]


def test_pick_stops_at_the_count_and_line_caps():
    many = [definition(f"name_{i}", first=i + 1) for i in range(MAX_DEFINITIONS + 3)]
    text = "int f(void) { return " + " + ".join(f"name_{i}" for i in range(MAX_DEFINITIONS + 3)) + "; }\n"

    assert len(pick_definitions(many, "f.c", text, 1, 1, [1])) == MAX_DEFINITIONS

    big = [definition("huge", lines=MAX_DEFINITION_TOTAL_LINES - 5), definition("more", lines=10)]
    text = "int f(void) { return huge + more; }\n"

    assert [d.name for d in pick_definitions(big, "f.c", text, 1, 1, [1])] == ["huge"]


def test_pick_is_deterministic():
    definitions = [definition("lookup", file="a.c", kind="variable"), definition("BUF_LEN")]

    first = pick_definitions(definitions, "src/use.c", REGION, 6, 10, [8])
    second = pick_definitions(list(reversed(definitions)), "src/use.c", REGION, 6, 10, [8])

    assert first == second
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: `ImportError: cannot import name 'pick_definitions'`

- [ ] **Step 3: 구현**

`src/llm_lab/csource.py`에 더한다.

```python
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
    같은 이름은 같은 파일 → 헤더 → 그 밖 순서로 PER_NAME개까지. 같은 입력에는 같은 결과.
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
    total = 0
    for name in wanted:
        candidates = sorted(by_name.get(name, ()), key=lambda d: (_rank(d, source_name), d.file, d.first))
        for definition in candidates[:PER_NAME]:
            size = definition.text.count("\n") + 1
            if len(picked) >= MAX_DEFINITIONS or total + size > MAX_DEFINITION_TOTAL_LINES:
                return picked
            picked.append(definition)
            total += size
    return picked
```

`_names(" ".join(near))`는 near의 이름을 처음 나온 순서로 한 번씩 남기는 용도다(이름은 식별자이므로 공백으로 이어도 그대로 다시 뽑힌다).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/csource.py tests/test_csource.py
git commit -m "feat: pick the definitions a request needs, nearest finding first"
```

---

### Task 5: 작업에 정의 저장 (스키마 4)

**Files:**
- Modify: `src/llm_lab/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `csource.Definition`
- Produces: `Store.create_job(..., definitions: Iterable[Definition] = ())`, `Store.definitions(job_id: int) -> list[Definition]`, `SCHEMA_VERSION == 4`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_store.py`에 더한다 (import에 `from llm_lab.csource import Definition`).

```python
DEFINITIONS = [
    Definition("BUF_LEN", "macro", "inc/limits.h", 3, 3, "#define BUF_LEN 16"),
    Definition("BUF_LEN", "macro", "inc/limits.h", 7, 7, "#define BUF_LEN 32"),
    Definition("lookup", "variable", "a.c", 3, 5, "static const u8 lookup[3] = {\n    1, 2, 3\n};"),
]


def test_schema_version_is_four():
    assert SCHEMA_VERSION == 4


def test_definitions_round_trip_in_name_file_line_order(tmp_path):
    store = Store(tmp_path / "fixer.db")
    rows = [NewRow(FINDING, "calc.c", "pending")]

    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows, definitions=reversed(DEFINITIONS))

    assert store.definitions(job_id) == DEFINITIONS
    assert store.definitions(job_id + 1) == []


def test_job_without_definitions_has_an_empty_list(tmp_path):
    store = Store(tmp_path / "fixer.db")

    job_id = make_job(store, "pending")

    assert store.definitions(job_id) == []
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_store.py`
Expected: `test_schema_version_is_four`는 `assert 3 == 4`로, 나머지는 `TypeError: ... unexpected keyword argument 'definitions'`로 FAIL

- [ ] **Step 3: 구현**

`src/llm_lab/store.py`:
- import에 `from collections.abc import Iterable, Iterator`(기존 `Iterator` 줄을 바꾼다)와 `from llm_lab.csource import Definition`을 더한다.
- 버전 주석 아래에 `# 4: definitions (분석 대상 파일이 쓰는 다른 곳의 정의)`를 더하고 `SCHEMA_VERSION = 4`.
- `SCHEMA` 문자열의 `cache` 표 뒤에 더한다:

```sql
CREATE TABLE IF NOT EXISTS definitions (
    job_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    first INTEGER NOT NULL,
    last INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, name, file, first)
);
```

- `create_job`의 매개변수 끝에 `definitions: Iterable[Definition] = ()`를 더하고, rows를 넣는 `executemany` 뒤에:

```python
            db.executemany(
                "INSERT OR IGNORE INTO definitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(job_id, d.name, d.kind, d.file, d.first, d.last, d.text) for d in definitions],
            )
```

- `source()` 메서드 뒤에:

```python
    def definitions(self, job_id: int) -> list[Definition]:
        """작업에 저장한 다른 곳의 정의. 이름·파일·줄 순서."""
        with self._db() as db:
            found = db.execute(
                "SELECT name, kind, file, first, last, text FROM definitions "
                "WHERE job_id = ? ORDER BY name, file, first",
                (job_id,),
            ).fetchall()
        return [Definition(**dict(record)) for record in found]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_store.py tests/test_web.py`
Expected: PASS (스키마 3 DB를 여는 기존 테스트도 그대로 통과한다. 표는 `CREATE TABLE IF NOT EXISTS`로 더해진다)

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/store.py tests/test_store.py
git commit -m "feat: jobs keep the definitions their sources use (schema 4)"
```

---

### Task 6: 요청에 정의 절 붙이기

**Files:**
- Modify: `src/llm_lab/fixer.py` (`Request`, `build_prompt`, `make_request`)
- Modify: `src/llm_lab/kinds/base.py` (`Kind.uses_definitions`)
- Modify: `src/llm_lab/kinds/codemetrics.py` (`uses_definitions=False`)
- Test: `tests/test_fixer.py`, `tests/test_kinds.py`

**Interfaces:**
- Consumes: `csource.Definition`, `csource.pick_definitions`
- Produces: `Request.definitions: tuple[Definition, ...]`, `build_prompt(kind, findings, source, first, last, context, definitions=())`, `make_request(kind, findings, source, context=None, definitions=())`, `Kind.uses_definitions: bool`(기본 True), `fixer.definitions_block(definitions) -> str`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_fixer.py`에 더한다 (import에 `from llm_lab.csource import Definition`, `from llm_lab.kinds import CODEMETRICS, MISRA, RTE`로 바꾼다).

```python
LIMIT = Definition("LIMIT", "macro", "inc/limits.h", 3, 3, "#define LIMIT 16")
USER = decode_source("use.c", b"int use(int b)\n{\n    return LIMIT / b;\n}\n")


def test_request_appends_the_definitions_the_code_uses():
    request = make_request(RTE, [finding(line=3)], USER, definitions=[LIMIT])

    assert request.definitions == (LIMIT,)
    content = request.messages[1]["content"]
    assert content.index("```c\n    1| int use(int b)") < content.index("Definitions from other places")
    assert "for reference only. Edits must stay in the code above." in content
    assert "```c\n// inc/limits.h line 3\n#define LIMIT 16\n```" in content


def test_request_without_matching_definitions_has_no_definitions_section():
    unused = Definition("OTHER", "macro", "inc/limits.h", 4, 4, "#define OTHER 1")

    request = make_request(RTE, [finding(line=3)], USER, definitions=[unused])

    assert request.definitions == ()
    assert "Definitions from other places" not in request.messages[1]["content"]


def test_definitions_change_the_cache_key():
    plain = make_request(RTE, [finding(line=3)], USER)
    with_limit = make_request(RTE, [finding(line=3)], USER, definitions=[LIMIT])

    assert cache_key(MODEL, plain) != cache_key(MODEL, with_limit)


def test_codemetrics_requests_ignore_definitions():
    assert CODEMETRICS.uses_definitions is False
    assert RTE.uses_definitions is True and MISRA.uses_definitions is True
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_fixer.py -k "definitions"`
Expected: `TypeError: make_request() got an unexpected keyword argument 'definitions'`와 `AttributeError: ... 'uses_definitions'`

- [ ] **Step 3: 구현**

`src/llm_lab/kinds/base.py`의 `Kind`에서 `timeout` 줄 아래에 더한다:

```python
    # 보내는 코드가 쓰는 다른 곳의 정의(함수·매크로·전역·타입)를 요청에 참고용으로 붙일지
    uses_definitions: bool = True
```

`src/llm_lab/kinds/codemetrics.py`의 `CODEMETRICS = Kind(` 인자에 `uses_definitions=False,  # 함수 하나를 나누는 일이라 정의가 필요 없다`를 더한다.

`src/llm_lab/fixer.py`:
- import를 `from llm_lab.csource import Definition, pick_definitions, split_lines, top_level_blocks`로 바꾼다.
- `Request`에 마지막 필드로 `definitions: tuple[Definition, ...] = ()  # 코드 뒤에 붙인 다른 곳의 정의`를 더한다.
- `build_prompt` 앞에:

```python
def definitions_block(definitions: tuple[Definition, ...]) -> str:
    """코드 블록 뒤에 붙이는 '다른 곳의 정의' 절. 줄 번호는 붙이지 않는다(수정 원문으로 베끼지 못하게)."""
    if not definitions:
        return ""
    parts = []
    for definition in definitions:
        where = f"line {definition.first}" if definition.first == definition.last else f"lines {definition.first}-{definition.last}"
        parts.append(f"```c\n// {definition.file} {where}\n{definition.text}\n```")
    return (
        "\n\nDefinitions from other places, for reference only. "
        "Edits must stay in the code above.\n\n" + "\n\n".join(parts)
    )
```

- `build_prompt`의 서명을 `build_prompt(kind, findings, source, first, last, context, definitions: tuple[Definition, ...] = ())`로 바꾸고, 반환 식의 끝 `f"```c\n{numbered(source.text, first, last)}\n```"` 뒤에 `+ definitions_block(definitions)`를 더한다.
- `make_request`의 서명을 `make_request(kind, findings, source, context=None, definitions: Iterable[Definition] = ())`로 바꾼다 (`from collections.abc import Iterable` 추가). `first, last = kind.region(...)` 뒤에:

```python
    picked: tuple[Definition, ...] = ()
    if kind.uses_definitions:
        lines = [finding.line for finding in findings if finding.line is not None]
        picked = tuple(pick_definitions(definitions, source.name, source.text, first, last, lines))
    messages = [
        {"role": "system", "content": kind.system_prompt},
        {
            "role": "user",
            "content": build_prompt(kind, findings, source, first, last, context, picked),
        },
    ]
    return Request(kind, list(findings), source, first, last, context, messages, picked)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_fixer.py tests/test_kinds.py tests/test_codemetrics_prompt.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/fixer.py src/llm_lab/kinds/base.py src/llm_lab/kinds/codemetrics.py tests/test_fixer.py
git commit -m "feat: requests carry the definitions the sent code uses, for reference"
```

---

### Task 7: 작업 만들기와 작업자에 잇기

**Files:**
- Modify: `src/llm_lab/web.py` (`_job_note`, `create_job` 처리기)
- Modify: `src/llm_lab/worker.py` (`_process`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `csource.scan_folder`, `csource.used_definitions`, `Store.create_job(definitions=)`, `Store.definitions`, `make_request(definitions=)`
- Produces: `_job_note(sheet, definitions: int = 0, unreadable: int = 0) -> str`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_web.py`에 더한다.

```python
USE_C = b'#include "limits.h"\nint divide(int a, int b)\n{\n    return a / (b % LIMIT);\n}\n'


def folder_with_header(tmp_path) -> Path:
    """calc.c가 inc/limits.h의 매크로를 쓰는 소스 폴더. 깨진 파일 하나가 함께 있다."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "inc").mkdir()
    (root / "src" / "calc.c").write_bytes(USE_C)
    (root / "inc" / "limits.h").write_bytes(b"#define LIMIT 4\n#define UNUSED 9\n")
    (root / "src" / "broken.c").write_bytes(b"int a;\r\nint b;\n")
    return root


def test_job_sends_definitions_from_other_files_and_notes_the_count(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))
    excel = rte_excel(rte_row("Orange Check", line=4))

    response = upload(client, excel, folder_with_header(tmp_path))

    assert response.status_code == 303
    content = sent[0]["messages"][1]["content"]
    assert "```c\n// inc/limits.h line 1\n#define LIMIT 4\n```" in content
    assert "UNUSED" not in content
    job = store.job(1)
    # calc.c 자신의 divide 정의도 저장된다(긴 파일에서 함수만 보낼 때 쓴다). 메모는 저장한 수다
    assert "참고 정의 2개" in job.note and "못 읽은 파일 1개" in job.note
    assert [d.name for d in store.definitions(1)] == ["LIMIT", "divide"]


def test_definitions_inside_the_sent_code_are_stored_but_not_sent(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer())

    upload(client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path))

    assert [d.name for d in store.definitions(1)] == ["divide"]
    assert "Definitions from other places" not in sent[0]["messages"][1]["content"]
    assert "참고 정의 1개" in store.job(1).note
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_web.py -k "definitions"`
Expected: 두 테스트 모두 FAIL (정의 절이 없고, `store.definitions(1)`이 비어 있다)

- [ ] **Step 3: 구현**

`src/llm_lab/web.py`:
- import에 `from llm_lab.csource import scan_folder, used_definitions`를 더한다.
- `_job_note`를 바꾼다:

```python
def _job_note(sheet: Sheet, definitions: int = 0, unreadable: int = 0) -> str:
    """작업 메모 한 줄: 종류가 엑셀 전체에서 읽은 메모, 뺀 행의 이유별 개수, 참고 정의 수."""
    parts = [sheet.prepared.note] if sheet.prepared.note else []
    if sheet.skipped:
        skipped = ", ".join(f"{reason} {count}" for reason, count in sheet.skipped.items())
        parts.append(f"제외: {skipped}")
    if definitions:
        parts.append(f"참고 정의 {definitions}개")
    if unreadable:
        parts.append(f"못 읽은 파일 {unreadable}개")
    return " · ".join(parts)
```

"다른 파일 정의"가 아니라 "참고 정의"인 이유: 같은 파일 안의 정의도 저장하므로(긴 파일에서 함수만 보낼 때 쓴다) 저장한 수에는 같은 파일 것도 들어간다.

- `create_job` 처리기에서 `files = [source for source in files if source.name in used]` 뒤에:

```python
        # 소스 폴더 전체의 정의 가운데 분석 대상 파일이 쓰는 것만 작업에 저장한다
        definitions, unreadable = [], 0
        if sheet_kind.uses_definitions:
            found, unreadable = scan_folder(root)
            definitions = used_definitions(found, [source.text for source in files])
```

그리고 `store.create_job(` 호출에 `definitions=definitions,`를 더하고 `note=_job_note(sheet)`를 `note=_job_note(sheet, len(definitions), unreadable)`로 바꾼다.

`src/llm_lab/worker.py`의 `_process`에서 `request = make_request(kind, findings, source, context)`를
`request = make_request(kind, findings, source, context, self.store.definitions(job_id))`로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_web.py tests/test_worker.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/web.py src/llm_lab/worker.py tests/test_web.py
git commit -m "feat: a job scans its source folder and sends the definitions each request uses"
```

---

### Task 8: 지시문, 문서, todo

**Files:**
- Modify: `src/llm_lab/kinds/rte.py`, `src/llm_lab/kinds/misra.py` (`SYSTEM_PROMPT`)
- Modify: `README.md`, `todo.md`
- Test: `tests/test_kinds.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (글만 바뀐다)

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_kinds.py`에 더한다 (`CODEMETRICS`를 import에 더한다).

```python
def test_rte_and_misra_prompts_explain_the_definitions_section():
    sentence = "Definitions of functions, macros, variables and types"
    assert sentence in RTE.system_prompt and sentence in MISRA.system_prompt
    assert "answer manual and name that file and function" in RTE.system_prompt
    assert "not in the code or the definitions shown" in RTE.system_prompt
    assert "Definitions of" not in CODEMETRICS.system_prompt
    assert RTE.prompt_version == 5 and MISRA.prompt_version == 2  # 답 처리는 그대로
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_kinds.py -k definitions_section`
Expected: FAIL (`sentence in RTE.system_prompt`가 False)

- [ ] **Step 3: 지시문 고치기**

`src/llm_lab/kinds/rte.py`의 `SYSTEM_PROMPT`에서
`- Otherwise answer manual: the cause is not in the code shown (the value comes from \`
`a caller, a definition or data that is not shown), or the only change you could make \`
를
`- Otherwise answer manual: the cause is not in the code or the definitions shown \`
`(the value comes from a caller or from data that is not shown), or the only change you could make \`
로 바꾼다. 그리고 `Do not guess definitions (macros, types, other functions) that are not shown. \` 줄 바로 앞에 더한다:

```
Definitions of functions, macros, variables and types that this code uses but that live \
elsewhere may follow the code, for reference. Use them to judge value ranges, array sizes, \
return values and the real types behind typedefs. Edits must stay in the code shown; \
if the right change is in one of those definitions or in a caller, \
answer manual and name that file and function in reason.
```

`src/llm_lab/kinds/misra.py`의 `SYSTEM_PROMPT`에서 `Do not guess definitions (macros, types, other functions) that are not shown. \` 줄 바로 앞에 같은 단락을 더한다. MISRA의 manual은 Mandatory에만 있으므로 "answer manual"은 그대로 둔다(Required·Advisory는 no_fix 규칙이 그대로다).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_kinds.py`
Expected: PASS

- [ ] **Step 5: 문서**

`README.md`의 "수정 제안 서비스" 절 2번 항목(소스 폴더와 시트 고르기) 아래 목록에 더한다:

```
   - 소스 폴더 전체의 `.c`·`.h`에서 함수·매크로·전역 변수·타입 정의를 찾아, 보내는 코드가 쓰는 것을 요청에 참고용으로 붙입니다(요청마다 12개·400줄까지). 작업 메모에 "참고 정의 N개"가 보입니다. 수정은 여전히 검사가 걸린 파일 안에서만 하고, 고칠 곳이 다른 파일이면 "직접 수정"으로 그 파일과 함수 이름을 이유에 적습니다.
```

`todo.md` 2절(RTE)과 3절(MISRA)의 "알려줄 것"에 각각 한 줄을 더한다:

```
- 작업 메모의 "참고 정의 N개"와 "못 읽은 파일 M개"의 숫자, "분석 시작"을 누른 뒤 결과 화면이 뜨기까지 걸린 시간
```

- [ ] **Step 6: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/kinds/rte.py src/llm_lab/kinds/misra.py tests/test_kinds.py README.md todo.md
git commit -m "feat: RTE and MISRA prompts use the definitions section; docs and todo follow"
```

---

## 끝낸 뒤

- `docs/superpowers/specs/2026-10-13-definitions-context-design.md`의 상태 줄에 구현 커밋을 적는다.
- origin master에 올린다(다른 세션이 올린 커밋이 있으면 base 브랜치 동기화로 합친 뒤 검사하고 올린다).
- 현장 결과(작업 메모의 두 숫자, 걸린 시간, Red·Orange의 직접 수정 개수 변화)를 받으면 다음을 정한다: 호출자 코드 보내기, Orange의 가드 규칙 손보기.
