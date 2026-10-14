# RTE 요청에 호출자 코드 함께 보내기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RTE 요청에 검사가 걸린 함수를 부르는 곳(호출 지점 주변 줄)을 고칠 수 있는 조각으로 함께 보낸다.

**Architecture:** `csource`가 함수 블록을 나열하고, 프로젝트 전체에서 검사 함수(와 그 별칭 매크로)를 부르는 줄을 찾아 `Definition(kind="caller")` 조각으로 만든다. 작업을 만들 때 조각과 그 파일 원문을 저장하고, `make_request`가 묶음의 함수에 맞는 조각을 골라 "Callers" 절로 붙이고 그 파일을 고칠 수 있게 한다. 수정 찾기는 기존 다른 파일 수정 범위(tier 2)에 조각 범위를 더한다.

**Tech Stack:** Python 3.14, uv, pytest, ruff, FastAPI, SQLite. 의존성 추가 없음.

**Spec:** `docs/superpowers/specs/2026-10-14-callers-context-design.md`

## Global Constraints

- 회사 값(파일·함수 이름, 개수, 임계값)을 코드·테스트·문서·커밋에 쓰지 않는다. 테스트 이름은 지어낸다.
- 커밋 전에 `uv run ruff format .`, `uv run ruff check .`, `uv run pytest -q`가 모두 통과해야 한다. 줄 길이 100칸, 한글은 2칸으로 센다.
- 커밋 메시지는 영어, 끝 줄은 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 파일의 원래 줄바꿈(CRLF/LF)을 지킨다.
- 상수: `CALLER_CONTEXT = 10`(호출 줄 위아래), `MAX_CALLERS = 4`(요청당 조각), `MAX_CALLER_LINES = 120`(요청당 조각 줄 합), `MAX_STORED_CALLERS = 20`(함수당 저장).
- 조각 하나는 `MAX_DEFINITION_LINES`(80)줄을 넘지 않게 나눈다.
- 호출자 절은 RTE(`Kind.sends_callers`)에서만, 그리고 조각 파일 원문이 작업에 있을 때만 붙는다. 없으면 요청 글은 지금과 같다.
- `pick_definitions`는 `kind == "caller"`를 고르지 않는다.
- 조각 순서: 검사 파일과 같은 파일 먼저, 그다음 파일 이름, 시작 줄.
- 모든 명령은 저장소 루트에서 `uv run …`.

## Review Focus

- 검사 함수가 자기 자신을 부르는 재귀: 조각을 만들지 않는다. (Task 1 테스트)
- 주석·문자열 안에 함수 이름이 있는 줄: 호출 지점이 아니다. (Task 1 테스트)
- 한 함수 안에서 같은 함수를 여러 번 부르는 경우: 겹치는 창은 하나로 합치되 80줄을 넘지 않는다. (Task 1 테스트)
- 호출자가 많아 요청 상한에 잘린 경우: 절 머리에 "N more call sites are not shown"이 붙는다. (Task 2 테스트)
- 예전 작업(조각 없음)이나 조각 파일 원문이 없는 작업: 호출자 절이 붙지 않고 요청 글이 지금과 같다. (Task 2 테스트)

---

### Task 1: 함수 블록, 호출 지점 찾기, 고르기 (`csource.py`)

**Files:**
- Modify: `src/llm_lab/csource.py`
- Test: `tests/test_csource.py`

**Interfaces:**
- Produces:
  - `function_blocks(text: str) -> list[tuple[str, int, int]]` — (함수 이름, 서명 첫 줄, 닫는 중괄호 줄)
  - `enclosing_function(text: str, line: int) -> str | None`
  - `find_call_sites(file: str, text: str, names: Mapping[str, str]) -> list[Definition]` — `names`는 부르는 이름 → 검사 함수 이름
  - `callers_for(definitions: Iterable[Definition], texts: Mapping[str, str], targets: Mapping[str, str]) -> list[Definition]` — `targets`는 검사 함수 이름 → 그 함수가 든 파일
  - `pick_callers(definitions: Iterable[Definition], function: str, source_name: str, available: Container[str] | None = None) -> tuple[list[Definition], int]` — (고른 조각, 고르지 못한 수)
  - `scan_project(root: Path) -> tuple[list[Definition], int, dict[str, str]]` — (정의, 못 읽은 파일 수, 파일 이름 → 원문)
  - 상수 `CALLER_CONTEXT`, `MAX_CALLERS`, `MAX_CALLER_LINES`, `MAX_STORED_CALLERS`
  - `scan_folder(root)`는 그대로 쓸 수 있다(`scan_project`를 부른다).

- [ ] **Step 1: 실패하는 테스트**

`tests/test_csource.py` 끝에 더한다. import 줄에 `CALLER_CONTEXT, MAX_CALLERS, MAX_CALLER_LINES, MAX_STORED_CALLERS, callers_for, enclosing_function, find_call_sites, function_blocks, pick_callers, scan_project`를 더한다.

```python
CALLEE = "int divide(int a, int b)\n{\n    return a / b;\n}\n"
CALLER_TEXT = (
    '#include "calc.h"\n'
    "int update(int sum)\n"
    "{\n"
    "    int n = read_count();\n"
    "    /* divide(sum, 0) in a comment */\n"
    "    return divide(sum, n);\n"
    "}\n"
    "int twice(int x)\n"
    "{\n"
    "    return DIV(x, 2) + divide(x, 3);\n"
    "}\n"
)
NAMES = {"divide": "divide", "DIV": "divide"}


def spans(definitions):
    return [(d.name, d.kind, d.file, d.first, d.last) for d in definitions]


def test_function_blocks_and_enclosing_function():
    assert function_blocks(CALLER_TEXT) == [("update", 2, 7), ("twice", 8, 11)]
    assert enclosing_function(CALLEE, 3) == "divide"
    assert enclosing_function(CALLEE, 1) == "divide"  # 서명 줄도 그 함수다
    assert enclosing_function("int g;\nint f(void)\n{\n}\n", 1) is None
    assert enclosing_function("struct s {\n    int a;\n};\n", 2) is None


def test_call_sites_skip_comments_and_merge_per_function():
    sites = find_call_sites("app/main.c", CALLER_TEXT, NAMES)

    assert spans(sites) == [
        ("divide", "caller", "app/main.c", 2, 7),
        ("divide", "caller", "app/main.c", 8, 11),
    ]
    assert sites[0].text.startswith("int update(int sum)\n{")


def test_recursion_is_not_a_call_site():
    text = "int divide(int a, int b)\n{\n    return b ? divide(a, b - 1) : a;\n}\n"

    assert find_call_sites("calc.c", text, {"divide": "divide"}) == []


def test_window_is_cut_to_the_function_and_long_bodies_split():
    body = "\n".join(f"    v = divide(v, {i});" for i in range(100))
    text = f"int run(int v)\n{{\n{body}\n    return v;\n}}\n"

    sites = find_call_sites("run.c", text, {"divide": "divide"})

    assert sites[0].first == 1
    assert sites[-1].last == 104
    assert all(d.last - d.first + 1 <= 80 for d in sites)
    assert all(a.last < b.first for a, b in zip(sites, sites[1:], strict=False))


def test_callers_for_follows_alias_macros_and_keeps_the_home_file_first():
    found = [Definition("DIV", "macro", "app/calc.h", 1, 1, "#define DIV(a, b) divide(a, b)")]
    texts = {
        "app/calc.c": CALLEE + "int half(int v)\n{\n    return divide(v, 2);\n}\n",
        "app/calc.h": "#define DIV(a, b) divide(a, b)\n",
        "app/main.c": CALLER_TEXT,
        "app/zz.c": "int other(int v)\n{\n    return v;\n}\n",
    }

    kept = callers_for(found, texts, {"divide": "app/calc.c"})

    assert [(d.file, d.first) for d in kept] == [
        ("app/calc.c", 5),
        ("app/main.c", 2),
        ("app/main.c", 8),
    ]


def test_callers_for_stores_at_most_the_cap_per_function():
    many = "".join(
        f"int c{i}(int v)\n{{\n    return divide(v, {i});\n}}\n"
        for i in range(MAX_STORED_CALLERS + 5)
    )

    kept = callers_for([], {"many.c": many}, {"divide": "calc.c"})

    assert len(kept) == MAX_STORED_CALLERS


def test_pick_callers_caps_count_and_lines_and_counts_the_rest():
    def caller(file, first, size):
        return Definition("divide", "caller", file, first, first + size - 1, "x")

    callers = [caller("b.c", 1, 10), caller("calc.c", 50, 10), caller("a.c", 1, 10)]
    callers += [caller("c.c", 10 * i, 10) for i in range(1, 5)]
    callers.append(Definition("other", "caller", "a.c", 1, 5, "x"))

    picked, hidden = pick_callers(callers, "divide", "calc.c")

    assert [(d.file, d.first) for d in picked] == [
        ("calc.c", 50),
        ("a.c", 1),
        ("b.c", 1),
        ("c.c", 10),
    ]
    assert len(picked) == MAX_CALLERS and hidden == 3

    big = [caller("a.c", 1, MAX_CALLER_LINES - 5), caller("b.c", 1, 10)]
    picked, hidden = pick_callers(big, "divide", "calc.c")
    assert [d.file for d in picked] == ["a.c"] and hidden == 1

    picked, _ = pick_callers(callers, "divide", "calc.c", available={"a.c"})
    assert [d.file for d in picked] == ["a.c"]


def test_pick_definitions_never_picks_caller_fragments():
    fragment = Definition("divide", "caller", "main.c", 2, 7, "int update(int sum)")
    text = "int divide(int a, int b)\n{\n    return a / b;\n}\n"

    assert pick_definitions([fragment], "calc.c", text, 1, 4, [3]) == []


def test_scan_project_returns_texts(tmp_path):
    (tmp_path / "a.c").write_bytes(b"int f(void)\n{\n    return 0;\n}\n")

    definitions, unreadable, texts = scan_project(tmp_path)

    assert [d.name for d in definitions] == ["f"]
    assert unreadable == 0 and texts == {"a.c": "int f(void)\n{\n    return 0;\n}\n"}
    assert CALLER_CONTEXT == 10
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: `ImportError: cannot import name 'CALLER_CONTEXT'`

- [ ] **Step 3: 구현**

`src/llm_lab/csource.py`:

1) 상수 근처(`MAX_DEFINITIONS` 위나 아래)에 더한다.

```python
CALLER_CONTEXT = 10  # 호출 줄 위아래로 이만큼을 호출자 조각으로 보낸다
MAX_CALLERS = 4  # 요청 하나에 붙이는 호출자 조각 수
MAX_CALLER_LINES = 120  # 요청 하나에 붙이는 호출자 조각 줄 수 합계
MAX_STORED_CALLERS = 20  # 검사 함수 하나에 저장하는 호출자 조각 수
```

2) `find_definitions` 아래에 더한다.

```python
def function_blocks(text: str) -> list[tuple[str, int, int]]:
    """파일의 함수 정의: (이름, 서명 첫 줄, 닫는 중괄호 줄). find_definitions와 같은 규칙."""
    code = split_lines(blank_comments(text))
    found: list[tuple[str, int, int]] = []
    for start, end in top_level_blocks(text):
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


def find_call_sites(file: str, text: str, names: Mapping[str, str]) -> list[Definition]:
    """names의 이름을 부르는 줄마다 위아래 CALLER_CONTEXT 줄을 그 함수 범위 안에서 잘라 조각으로.

    조각의 name은 부른 이름이 가리키는 검사 함수다. 검사 함수 자신 안의 호출(재귀)은 뺀다.
    한 함수 안에서 겹치거나 붙은 창은 합치되 MAX_DEFINITION_LINES를 넘지 않게 나눈다.
    """
    lines = split_lines(text)
    code = split_lines(blank_comments(text))
    sites: list[Definition] = []
    for caller, first, end in function_blocks(text):
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
```

3) `used_definitions` 아래(별칭 함수 `macro_target` 정의 뒤)에 더한다.

```python
def callers_for(
    definitions: Iterable[Definition], texts: Mapping[str, str], targets: Mapping[str, str]
) -> list[Definition]:
    """검사 함수(targets: 이름 → 그 함수가 든 파일)를 부르는 조각. 별칭 매크로를 통한 호출도 센다.

    함수마다 같은 파일 먼저, 그다음 파일 이름·시작 줄 순서로 MAX_STORED_CALLERS개까지.
    """
    names = {name: name for name in targets}
    for definition in definitions:
        target = macro_target(definition)
        if target in targets and definition.name not in names:
            names[definition.name] = target
    sites = [
        site for file, text in sorted(texts.items()) for site in find_call_sites(file, text, names)
    ]
    kept: list[Definition] = []
    for target, home in sorted(targets.items()):
        mine = sorted(
            (site for site in sites if site.name == target),
            key=lambda site: (site.file != home, site.file, site.first),
        )
        kept.extend(mine[:MAX_STORED_CALLERS])
    return kept


def pick_callers(
    definitions: Iterable[Definition],
    function: str,
    source_name: str,
    available: Container[str] | None = None,
) -> tuple[list[Definition], int]:
    """function을 부르는 조각을 요청 상한 안에서 고른다: (고른 것, 고르지 못한 수).

    available을 주면 그 파일의 조각만 본다(원문이 작업에 있는 파일).
    """
    mine = sorted(
        (
            d
            for d in definitions
            if d.kind == "caller"
            and d.name == function
            and (available is None or d.file in available)
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
```

4) `scan_folder`를 바꾼다.

```python
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
```

5) `pick_definitions`의 `for definition in definitions:` 반복 첫 줄에 더한다.

```python
        if definition.kind == "caller":
            continue  # 호출자 조각은 pick_callers가 따로 고른다
```

6) import: `from collections.abc import Container, Iterable, Mapping`.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_csource.py`
Expected: PASS. `test_window_is_cut_to_the_function_and_long_bodies_split`이 틀리면 창 합치기·나누기 규칙만 고친다(조각이 함수 범위 안, 80줄 이하, 서로 겹치지 않고, 첫 조각은 서명 줄에서, 마지막은 닫는 중괄호 줄에서 끝나야 한다).

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/csource.py tests/test_csource.py
git commit -m "feat: find call sites of a function across the project as caller fragments"
```

---

### Task 2: 요청에 호출자 절을 붙이고 조각 안 수정을 받는다

**Files:**
- Modify: `src/llm_lab/kinds/base.py` (`Kind.sends_callers`)
- Modify: `src/llm_lab/kinds/rte.py` (`sends_callers=True`)
- Modify: `src/llm_lab/fixer.py` (`Request.callers`, `callers_block`, `build_prompt`, `make_request`, `_ranges`)
- Test: `tests/test_fixer.py`

**Interfaces:**
- Consumes: `csource.enclosing_function`, `csource.pick_callers` (Task 1)
- Produces: `Request.callers: tuple[Definition, ...]`, `callers_block(function, callers, hidden, files) -> str`, `build_prompt(..., callers_text: str = "")`, `Kind.sends_callers: bool = False`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_fixer.py` 끝에 더한다.

```python
CALC = decode_source("app/calc.c", b"int divide(int a, int b)\n{\n    return a / b;\n}\n")
MAIN = decode_source(
    "app/main.c",
    b"int update(int sum)\n{\n    int n = read_count();\n    return divide(sum, n);\n}\n",
)
UPDATE = Definition("divide", "caller", "app/main.c", 1, 5, MAIN.text.rstrip("\n"))
FILES = {"app/calc.c": CALC, "app/main.c": MAIN}


def caller_request(kind=RTE, definitions=(UPDATE,), files=FILES):
    return make_request(kind, [finding(line=3)], CALC, None, definitions, files)


def test_rte_request_carries_editable_callers():
    request = caller_request()

    content = request.messages[1]["content"]
    assert "Callers of divide(), for the values that reach its parameters." in content
    assert (
        "// editable caller: app/main.c lines 1-5 (in update())\n    1| int update(int sum)"
        in content
    )
    assert request.callers == (UPDATE,)
    assert "app/main.c" in request.files


def test_no_caller_section_without_fragments_files_or_for_misra():
    plain = make_request(RTE, [finding(line=3)], CALC)

    for request in (
        caller_request(definitions=()),
        caller_request(files={"app/calc.c": CALC}),
        make_request(MISRA, [misra_finding(line=3)], CALC, None, (UPDATE,), FILES),
    ):
        assert "Callers of" not in request.messages[1]["content"]
        assert request.callers == ()
    assert caller_request(definitions=()).messages == plain.messages


def test_caller_section_says_how_many_call_sites_are_hidden():
    many = tuple(Definition("divide", "caller", "app/main.c", 1, 5, "x") for _ in range(1)) + tuple(
        Definition("divide", "caller", f"app/m{i}.c", 1, 5, "x") for i in range(5)
    )
    files = {**FILES, **{f"app/m{i}.c": replace(MAIN, name=f"app/m{i}.c") for i in range(5)}}

    request = caller_request(definitions=many, files=files)

    assert "2 more call sites are not shown." in request.messages[1]["content"]
    assert len(request.callers) == 4


def test_fix_in_a_caller_is_located_in_its_fragment():
    guard = {
        "file": "app/main.c",
        "original": "    int n = read_count();",
        "replacement": "    int n = read_count();\n    if (n == 0) {\n        n = 1;\n    }",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[guard]))

    (result,) = propose_fixes(make_client(handler), MODEL, caller_request())

    assert [edit.file for edit in result.edits] == ["app/main.c"]
    assert "--- a/app/main.c" in result.diff and "+    if (n == 0) {" in result.diff


def test_fix_in_a_caller_file_outside_its_fragment_is_asked_again():
    fragment = Definition("divide", "caller", "app/main.c", 4, 5, "    return divide(sum, n);\n}")
    outside = {"file": "app/main.c", "original": "    int n = read_count();", "replacement": "x"}
    inside = {
        "file": "app/main.c",
        "original": "    return divide(sum, n);",
        "replacement": "    return divide(sum, n ? n : 1);",
    }
    handler, sent = scripted(
        answer(("fix", REASON), edits=[outside]), answer(("fix", REASON), edits=[inside])
    )

    (result,) = propose_fixes(make_client(handler), MODEL, caller_request(definitions=(fragment,)))

    assert len(sent) == 2
    assert "n ? n : 1" in result.diff
```

`replace`(dataclasses), `Definition`, `MISRA`, `misra_finding`, `decode_source`, `answer`, `REASON`, `scripted`, `make_client`, `MODEL`, `propose_fixes`가 test_fixer.py에 이미 import되어 있는지 확인하고, 없으면 더한다. `SourceFile`은 frozen dataclass라 `replace(MAIN, name=...)`이 된다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_fixer.py -k "caller"`
Expected: FAIL (`"Callers of divide()" in content`가 거짓)

- [ ] **Step 3: 구현**

`src/llm_lab/kinds/base.py`의 `Kind`에서 `edits_definitions` 아래:

```python
    # 검사 함수를 부르는 곳(호출자 조각)을 요청에 붙일지
    sends_callers: bool = False
```

`src/llm_lab/kinds/rte.py`의 `RTE = Kind(` 인자에 `sends_callers=True,`를 더한다.

`src/llm_lab/fixer.py`:

1) import에 `enclosing_function`, `pick_callers`를 `llm_lab.csource`에서 더한다.

2) `Request`의 마지막 필드 뒤:

```python
    callers: tuple[Definition, ...] = ()  # 붙인 호출자 조각 (고칠 수 있음)
```

3) `definitions_block` 아래:

```python
def callers_block(
    function: str, callers: tuple[Definition, ...], hidden: int, files: Mapping[str, SourceFile]
) -> str:
    """검사 함수를 부르는 곳의 절. 조각마다 원래 줄 번호를 붙여 고칠 수 있게 보낸다."""
    if not callers:
        return ""
    more = f" {hidden} more call sites are not shown." if hidden else ""
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
```

4) `build_prompt` 끝 매개변수로 `callers_text: str = ""`를 더하고, 반환 글의 `+ definitions_block(definitions, editable)` 뒤에 `+ callers_text`를 붙인다.

5) `make_request`:
- 맨 앞(검사 줄 확인 반복 전)에 `definitions = list(definitions)`를 둔다(아래에서 두 번 읽는다).
- `editable = {...}`를 정한 뒤, `messages = [...]` 앞에:

```python
    callers: tuple[Definition, ...] = ()
    callers_text = ""
    if kind.sends_callers and files:
        line = next((f.line for f in findings if f.line is not None), None)
        function = enclosing_function(source.text, line) if line is not None else None
        if function:
            found, hidden = pick_callers(definitions, function, source.name, files)
            callers = tuple(found)
            editable = {**editable, **{c.file: files[c.file] for c in callers}}
            callers_text = callers_block(function, callers, hidden, files)
```

- `build_prompt(...)` 호출에 `callers_text`를 마지막 인자로 넘기고, `Request(...)`에 `callers`를 마지막 인자로 넘긴다.

6) `_ranges`의 두 번째 묶음에 조각 범위를 더한다.

```python
    if target in request.files:
        tiers.append(
            [(d.first, _shown_last(d)) for d in request.definitions if d.file == target]
            + [(c.first, c.last) for c in request.callers if c.file == target]
        )
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_fixer.py tests/test_fixer_edits.py tests/test_kinds.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/fixer.py src/llm_lab/kinds/base.py src/llm_lab/kinds/rte.py tests/test_fixer.py
git commit -m "feat: RTE requests carry editable caller fragments of the checked function"
```

---

### Task 3: 작업을 만들 때 호출자 조각과 그 파일을 저장한다

**Files:**
- Modify: `src/llm_lab/web.py` (`_job_note`, `create_job` 처리기)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `csource.scan_project`, `csource.callers_for`, `csource.enclosing_function` (Task 1), `Kind.sends_callers` (Task 2)
- Produces: 작업 메모의 `호출 지점 N곳`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_web.py` 끝에 더한다.

```python
def project_with_a_caller(tmp_path) -> Path:
    """app/src/calc.c의 divide를 app/src/main.c의 update가 부르는 프로젝트 폴더."""
    top = tmp_path / "proj"
    (top / "app" / "src").mkdir(parents=True)
    (top / "app" / "src" / "calc.c").write_bytes(CALC_C)
    (top / "app" / "src" / "main.c").write_bytes(
        b"int update(int sum)\n{\n    int n = read_count();\n    return divide(sum, n);\n}\n"
    )
    return top


def test_rte_job_sends_callers_and_notes_the_count(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))

    upload(client, rte_excel(rte_row("Orange Check")), project_with_a_caller(tmp_path))

    content = sent[0]["messages"][1]["content"]
    assert "Callers of divide()" in content
    assert "// editable caller: app/src/main.c lines 1-5 (in update())" in content
    assert "app/src/main.c" in store.sources(1)
    assert "호출 지점 1곳" in store.job(1).note


def test_job_without_callers_keeps_its_note(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer())

    upload(client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path))

    assert "호출 지점" not in store.job(1).note
    assert "Callers of" not in sent[0]["messages"][1]["content"]
```

`CALC_C`(rte_fixtures)는 `int divide(int a, int b)\n{\n    return a / b;\n}\n`이고 `rte_row`의 기본 파일 경로 끝이 `src/calc.c`라 `app/src/calc.c`와 짝지어진다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_web.py -k "callers"`
Expected: `assert "Callers of divide()" in content` FAIL

- [ ] **Step 3: 구현**

`src/llm_lab/web.py`:

1) import: `from llm_lab.csource import callers_for, enclosing_function, scan_project, used_definitions` (`scan_folder` import는 쓰지 않으면 지운다).

2) `_job_note`에 `callers: int = 0` 매개변수를 끝에 더하고, `참고 정의` 부분 뒤에:

```python
    if callers:
        parts.append(f"호출 지점 {callers}곳")
```

3) 정의 블록을 바꾼다.

```python
        definitions, unreadable, callers = [], 0, []
        if sheet_kind.uses_definitions:
            found, unreadable, texts = scan_project(root)
            definitions = used_definitions(found, [source.text for source in files])
            if sheet_kind.sends_callers:
                # 행마다 검사 줄을 품은 함수를 정하고, 프로젝트 전체에서 그 함수를 부르는 곳을 찾는다
                by_name = {source.name: source for source in files}
                targets: dict[str, str] = {}
                for new_row in rows:
                    source = by_name.get(new_row.file_name or "")
                    if source is None or new_row.finding.line is None:
                        continue
                    name = enclosing_function(source.text, new_row.finding.line)
                    name = name or new_row.finding.function.removesuffix("()").strip()
                    if name:
                        targets.setdefault(name, source.name)
                callers = callers_for(found, texts, targets)
            if sheet_kind.edits_definitions:
                # 고칠 수 있게 보낼 정의·호출자가 든 파일의 원문도 작업에 저장한다
                known = {source.name for source in files}
                for name in sorted({d.file for d in [*definitions, *callers]} - known):
                    try:
                        files.append(decode_source(name, (root / name).read_bytes()))
                    except DecodeError, OSError:
                        continue
```

(`rows`의 원소는 `NewRow`이고 `file_name`, `finding`을 가진다. 기존 변수 이름과 겹치면 반복 변수 이름을 바꾼다.)

4) `store.create_job(...)` 호출에서 `definitions=definitions`를 `definitions=[*definitions, *callers]`로, `_job_note(...)`에 `callers=len(callers)`를 더한다. 참고 정의 개수는 지금처럼 `definitions`만 센다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_web.py tests/test_worker.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/web.py tests/test_web.py
git commit -m "feat: RTE jobs find and store the callers of their checked functions"
```

---

### Task 4: RTE 지시문과 문서

**Files:**
- Modify: `src/llm_lab/kinds/rte.py` (`_CALLERS` 추가, `_DEFINITIONS_GUARD`, `_DEFINITIONS_ROOT`, `GUARD_PROMPT`, `ROOT_CAUSE_PROMPT`)
- Modify: `README.md`, `todo.md`
- Test: `tests/test_kinds.py`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_kinds.py` 끝에 더한다.

```python
def test_rte_prompts_explain_callers():
    for mode in ("guard", "root_cause"):
        prompt = RTE.prompt({"mode": mode})
        assert "Callers of the function may follow" in prompt
        assert "which values really reach its parameters" in prompt
        assert "If every caller shown guarantees safety and none are said to be not shown" in prompt
        assert "you may fix it in that caller, where the value is produced" in prompt
    guard = RTE.prompt({"mode": "guard"})
    assert "changing an editable caller or definition would be risky" in guard
    root = RTE.prompt({"mode": "root_cause"})
    assert "If the cause is in a caller that is not shown" in root
    assert "Callers of" not in MISRA.system_prompt
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_kinds.py -k callers`
Expected: FAIL

- [ ] **Step 3: 지시문 고치기**

`src/llm_lab/kinds/rte.py`에 `_FIX_RULES` 아래 새 조각을 더한다.

```python
_CALLERS = """\
Callers of the function may follow the definitions. Use them to see which values really reach \
its parameters. If every caller shown guarantees safety and none are said to be not shown, \
answer no_fix and name the caller lines. If a caller passes an unsafe value, \
you may fix it in that caller, where the value is produced, and set "file" to that path.
"""
```

`_DEFINITIONS_GUARD`를 다음으로 바꾼다.

```python
_DEFINITIONS_GUARD = """\
If the cause is in a caller that is not shown or in a definition marked reference only, \
or changing an editable caller or definition would be risky, \
guard the operation here and name that file and function in reason.
"""
```

`_DEFINITIONS_ROOT`를 다음으로 바꾼다.

```python
_DEFINITIONS_ROOT = """\
If the cause is in a caller that is not shown or in a definition marked reference only, \
answer manual and name that file and function in reason.
"""
```

조립 순서를 바꾼다.

```python
GUARD_PROMPT = _HEAD + GUARD_RULES + _FIX_RULES + _CALLERS + _DEFINITIONS_GUARD + _TAIL
ROOT_CAUSE_PROMPT = _HEAD + ROOT_CAUSE_RULES + _FIX_RULES + _CALLERS + _DEFINITIONS_ROOT + _TAIL
```

다른 테스트가 바뀐 문장을 단언하면(`grep -n "guard the operation here\|answer manual and name" tests/`), 새 글에도 들어 있는 부분 문자열로 그대로 맞는지 보고, 안 맞으면 최소한으로 고친다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_kinds.py tests/test_fixer.py`
Expected: PASS. `uv run python -c "from llm_lab.kinds import RTE; print(RTE.prompt({'mode':'guard'}))"`로 이어 붙인 글이 자연스러운지 한 번 읽는다.

- [ ] **Step 5: 문서**

`README.md`의 RTE "다른 파일 수정" 문장 뒤에 더한다:

```
   - RTE는 검사가 걸린 함수를 부르는 곳(호출 지점 위아래 10줄)도 프로젝트 전체에서 찾아 함께 보냅니다(요청마다 4곳·120줄까지, 함수당 20곳까지 저장). 들어오는 값이 안전하면 "수정 불필요"로, 호출자가 위험한 값을 넘기면 호출자 쪽을 고치는 수정안이 나올 수 있습니다. 작업 메모에 "호출 지점 N곳"이 보입니다. 함수 포인터로 부르는 곳은 찾지 못합니다.
```

`todo.md` 2절 알려줄 것에 더한다:

```
- 작업 메모의 "호출 지점 N곳", 호출자를 고친 수정 행 수, "수정 불필요" 가운데 이유가 호출자를 근거로 든 행 수
```

- [ ] **Step 6: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/kinds/rte.py tests/test_kinds.py README.md todo.md
git commit -m "feat: RTE prompts use caller fragments; docs and todo follow"
```

---

## 끝낸 뒤

- 설계 문서 상태 줄에 구현 커밋을 적는다.
- origin master에 올린다(다른 세션의 커밋이 있으면 base 브랜치 동기화로 합친 뒤 검사하고 올린다).
- 현장 숫자(호출 지점 수, 수정 불필요 변화, 호출자 수정 행 수)를 받으면 2단계 호출자나 함수 포인터를 정한다.
