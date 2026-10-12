# CodeMetrics 리팩터링 제안 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polyspace 결과 엑셀의 CodeMetrics 시트를 시트 종류 기반 위에 종류 하나(`codemetrics`)로 더한다. 함수마다 LLM을 한 번 불러 함수 범위를 통째로 다시 쓴 코드를 받고, 바뀐 줄만 남겨 diff·패치로 낸다.

**Architecture:**
- `src/llm_lab/cfunction.py`(새 모듈)는 C 소스에서 함수 범위(바로 위 주석부터 닫는 `}`까지)를 찾고, LLM이 낸 코드의 시그니처와 중괄호를 기계로 확인한다.
- `src/llm_lab/kinds/codemetrics.py`(새 모듈)는 기반의 `Kind` 훅을 채운다.
  - 엑셀 읽기: `prepare`, `read_row`. 조건 시트 단계, 지표 이름 짝, 한도 표를 만든다.
  - 묶음: 같은 함수.
  - 메시지: `facts`, `prompt_tail`, `region`.
  - 답 처리: `answer_schema`, `to_results`.
  - 그 밖: `auto_select`, `timeout`.
- 공통 코드(polyspace, fixer, store, worker, web, pages, transcript, patch)와 `conditions.py`는 고치지 않는다. `kinds/__init__.py`의 `KINDS`에 종류 하나를 더할 뿐이다.

**Tech Stack:** Python 3.14, uv, openpyxl, FastAPI(TestClient), difflib, pytest, ruff. 새 의존성은 없다.

**Spec:** `docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md`

**선행 조건:** 시트 종류 기반(`docs/superpowers/plans/2026-10-08-sheet-kinds-misra.md`)이 master에 올라와 있어야 한다. 아래 "시작 전 확인"을 먼저 한다.

## Global Constraints

- Python `>=3.14`이고, 명령은 모두 `uv run …`으로 실행한다.
- ruff: `line-length = 100`(한글은 폭 2로 센다), 규칙 `E, F, I, UP, B`. 태스크 끝마다 `uv run ruff check`와 `uv run ruff format --check`가 통과해야 한다.
- 코드 주석·docstring·화면 문구는 한국어로 쓴다. LLM 시스템 메시지는 영어로 쓰고 `reason`만 한국어로 받는다.
- 테스트는 네트워크를 쓰지 않는다. LLM은 `tests/fake_llm.py`로 흉내 낸다.
- 공개 저장소다. 회사 엑셀의 값(시트 이름, 행 수, 기준치, 지표·파일·함수 이름, 판정 글)을 코드, 테스트, 문서, 커밋에 넣지 않는다. 테스트 값은 만들어 낸 것만 쓴다.
- 조건(지표 목록, 한도, 단계 수, 판정 글, 열 배치)은 코드, 지시문, 테스트에 고정하지 않고 작업마다 엑셀에서 읽는다. `TIPS`는 Polyspace 지표 뜻에 대한 공개된 지식이고, 검사할 지표 목록이 아니다.
- "클수록 나쁨" 같은 방향을 가정하지 않는다. 단계는 조건 시트의 범위로만 정한다.
- 기반 파일(`polyspace.py`, `fixer.py`, `store.py`, `worker.py`, `web.py`, `pages.py`, `transcript.py`, `kinds/base.py`)과 `patch.py`, `conditions.py`는 고치지 않는다.
- 사용자에게 캡처를 부탁하는 화면과 todo의 "알려줄 것"에는 개수와 종류만 받는다. 코드, 이름, 경로는 받지 않는다(전역 규칙).
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`을 붙인다.

## Review Focus

설계가 직접 말하지 않지만 실제로 부딪히기 쉬운 경우 다섯 가지다. 각각을 맡은 태스크에 테스트로 고정했다.

1. **LLM이 보인 코드의 줄 번호 표시(`   57| `)까지 `code`에 베껴 옴:** 떼고 받아들여야 한다. (Task 4 `test_line_number_prefixes_copied_from_the_shown_code_are_removed`)
2. **LLM이 함수 위 주석을 지움:** 받지 않고 다시 묻는다. 두 번째도 그러면 화면 첫 줄이 "LLM이 함수 위 주석을 지웠습니다."다. (Task 4 `test_broken_code_is_asked_again_and_then_explained`)
3. **CP949 소스와 CRLF 소스:** 화면이 안내하는 명령으로 패치가 바이트 그대로 적용되어야 한다. (Task 5 `test_patch_applies_byte_for_byte_to_cp949_and_crlf_files`)
4. **한 파일에 같은 이름 함수가 두 번(`#if` 갈래):** 엑셀 줄에 있는 정의를 골라야 한다. (Task 1 `test_same_name_twice_picks_the_definition_at_the_line`)
5. **엑셀 Function 칸의 모양 차이(`foo()`, ` foo ( void ) `):** 같은 함수로 묶이고 소스에서 찾아져야 한다. (Task 1 `test_function_name_drops_parentheses_and_spaces`, Task 2 `test_kind_groups_by_function_and_leaves_middle_rows_for_people`)

---

## 이 계획의 코드

- 각 단계의 코드는 저장소 루트에서 `git apply`로 적용하는 diff다. 블록 내용을 파일로 저장해 `git apply 파일`을 실행하거나, 같은 내용을 손으로 고친다.
- 계획을 쓸 때 임시 사본에서 확인했다. 사본은 master `1fa598d`에 기반 계획(`2026-10-08-sheet-kinds-misra.md`)의 diff를 모두 적용한 것이다(테스트 372개 통과). 그 위에서 태스크마다 "테스트 diff 적용 → 실패 확인 → 구현 diff 적용 → 통과·린트 확인"을 되풀이했고, 결과 트리는 태스크 커밋과 같았다.
- 통과한 테스트 수는 그 사본 기준이다. 기반이나 다른 작업이 테스트를 더했으면 그만큼 많다. 실패가 0이면 된다.
- 작업 폴더가 CRLF(Git for Windows 기본 `core.autocrlf=true`)여도 `git apply`가 맞춰 적용한다.
- 스크립트로 파일을 고칠 때 Windows의 `Path.write_text`는 줄바꿈을 CRLF로 바꾼다. `newline="\n"`을 주거나 편집기로 고친다.

## 시작 전 확인 (기반 도착)

- [ ] **Step 1: master를 받는다**

```bash
git fetch origin
```

작업 브랜치를 origin/master 위로 맞춘다(워크트리면 앱의 base branch 동기화 기능, 아니면 `git rebase origin/master`).

- [ ] **Step 2: 기반이 이 계획이 쓰는 이름 그대로인지 확인한다**

```bash
uv run python -c "import dataclasses; from llm_lab.kinds.base import Cell, Kind; from llm_lab.polyspace import Finding, Prepared, cell_text, data_rows, find_header, line_number, norm, read_sheet; from llm_lab.fixer import FIX_SCHEMA, NUMBER_PREFIX, SIGNATURE_LINES, WHOLE_FILE_MAX_LINES, Edit, EditMismatch, FixError, FixResult, Request, _minimize, _top_level_blocks, apply_edits, display_diff, make_request, propose_fixes, read_decisions, share, split_lines, user_error; from llm_lab.conditions import Metric, read_metrics; need = {'prepare', 'group_key', 'facts', 'prompt_tail', 'region', 'answer_schema', 'to_results', 'timeout', 'auto_select'}; assert need <= {f.name for f in dataclasses.fields(Kind)}; assert {'category', 'note', 'threshold', 'actual', 'stage'} <= {f.name for f in dataclasses.fields(Finding)}; print('ok')"
```

Expected: `ok`

- 다르면 이 계획의 diff에서 바뀐 이름만 맞춰 적용한다.
- 함수 모양도 확인한다. 이 계획은 다음 모양을 쓴다.
  - `Kind.prompt_tail(context, findings, source)`
  - `Kind.to_results(data, request)`
  - `EditMismatch(detail, kind, summary)`
  - `read_decisions(data, count)`, `share(decisions, edits, diff)`
  - `make_request(kind, findings, source, context)`, `propose_fixes(client, model, request)`

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `src/llm_lab/cfunction.py` | C 함수 범위 찾기, 시그니처·중괄호 확인 | 1 |
| `src/llm_lab/kinds/codemetrics.py` | CodeMetrics 종류: 엑셀 읽기, 메시지, 답 처리 | 2, 3, 4 |
| `src/llm_lab/kinds/__init__.py` | `KINDS`에 `CODEMETRICS`를 더함 | 2 |
| `tests/codemetrics_fixtures.py` | CodeMetrics 시험 데이터(만들어 낸 값) | 2 |
| `tests/test_cfunction.py` | 함수 범위 테스트 | 1 |
| `tests/test_codemetrics_sheet.py` | 엑셀 읽기, 단계, 이름 짝, 한도 표 | 2 |
| `tests/test_codemetrics_prompt.py` | LLM 메시지와 보낼 범위 | 3 |
| `tests/test_codemetrics_answer.py` | 답 처리와 다시 묻기 | 4 |
| `tests/test_codemetrics_web.py` | 웹 흐름과 패치 적용 | 5 |
| `README.md`, CodeMetrics 설계 문서, `todo.md` | 문서와 회사 PC 확인 절 | 6 |

---

### Task 1: 함수 범위와 코드 확인 도우미 (`cfunction.py`)

**Files:**
- Create: `src/llm_lab/cfunction.py`
- Test: `tests/test_cfunction.py`

**Interfaces:**
- Consumes: `fixer.SIGNATURE_LINES`, `fixer._top_level_blocks(text) -> list[tuple[int, int]]`, `fixer.split_lines(text) -> list[str]`
- Produces:
  - `FunctionRange(start: int, signature: int, open_line: int, end: int)` (frozen). 바꿀 범위 첫 줄(바로 위 주석 포함), 시그니처 첫 줄, `{` 줄, 닫는 `}` 줄이다(1부터).
  - `function_range(text: str, line: int, name: str) -> FunctionRange | None`
  - `function_name(text: str) -> str`: 엑셀 Function 칸 → 함수 이름
  - `signature_text(text: str, found: FunctionRange) -> str`
  - `squash_code(code: str) -> str`: 주석 제거, 공백 정리, 기호 앞뒤 공백 제거
  - `braces_balanced(code: str) -> bool`

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_cfunction.py b/tests/test_cfunction.py
new file mode 100644
index 0000000..41e5641
--- /dev/null
+++ b/tests/test_cfunction.py
@@ -0,0 +1,89 @@
+from llm_lab.cfunction import (
+    FunctionRange,
+    braces_balanced,
+    function_name,
+    function_range,
+    signature_text,
+    squash_code,
+)
+
+ALLMAN = """\
+#include "a.h"
+
+/* 들어온 값을 고른다.
+   가운데 줄이 '*'로 시작하지 않는 블록 주석 */
+static int
+pick(int a, int b)
+{
+    if (a > b) { return a; } /* '{' in comment */
+    return b;
+}
+"""
+
+KR = """\
+int counter;
+// 더한다
+int add(int a, int b) {
+    const char *s = "}{";
+    return a + b + (s[0] == '}');
+}
+"""
+
+
+def test_finds_signature_above_brace_and_the_whole_leading_comment():
+    found = function_range(ALLMAN, 6, "pick")
+
+    assert found == FunctionRange(start=3, signature=5, open_line=7, end=10)
+
+
+def test_line_inside_the_body_or_on_the_brace_finds_the_same_function():
+    assert function_range(ALLMAN, 8, "pick") == function_range(ALLMAN, 7, "pick")
+
+
+def test_brace_on_the_signature_line_and_braces_in_strings():
+    assert function_range(KR, 3, "add") == FunctionRange(start=2, signature=3, open_line=3, end=6)
+
+
+def test_comment_split_by_a_blank_line_is_not_part_of_the_function():
+    text = "/* file header */\n\nint f(void)\n{\n    return 0;\n}\n"
+
+    assert function_range(text, 3, "f") == FunctionRange(start=3, signature=3, open_line=4, end=6)
+
+
+def test_same_name_twice_picks_the_definition_at_the_line():
+    text = (
+        "#if A\nstatic int g(void)\n{\n    return 1;\n}\n"
+        "#else\nstatic int g(void)\n{\n    return 2;\n}\n#endif\n"
+    )
+
+    assert function_range(text, 7, "g") == FunctionRange(start=7, signature=7, open_line=8, end=10)
+
+
+def test_other_name_or_no_block_is_none():
+    assert function_range(ALLMAN, 6, "other") is None
+    assert function_range("int x;\n", 1, "x") is None
+
+
+def test_function_name_drops_parentheses_and_spaces():
+    assert [function_name(t) for t in ("foo()", " foo ( void ) ", "foo", "")] == [
+        "foo",
+        "foo",
+        "foo",
+        "",
+    ]
+
+
+def test_signature_text_ignores_comments_and_layout():
+    found = function_range(ALLMAN, 6, "pick")
+    assert found is not None
+
+    # 공백은 한 칸으로 줄이고, 기호 앞뒤 공백은 없앤다 (줄바꿈·띄어쓰기 차이를 같은 것으로 본다)
+    assert signature_text(ALLMAN, found) == "static int pick(int a,int b)"
+    assert signature_text(ALLMAN, found) in squash_code("static int  pick( int a,\n int b) /* x */")
+    assert signature_text(ALLMAN, found) not in squash_code("static int pick(int a, long b)")
+
+
+def test_braces_balanced_skips_comments_strings_and_characters():
+    assert braces_balanced("int f(void)\n{\n    /* } */ return '{' + \"}\"[0];\n}\n")
+    assert not braces_balanced("int f(void)\n{\n    if (x) {\n}\n")
+    assert not braces_balanced("}{")
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_cfunction.py -q`
Expected: `ERROR tests/test_cfunction.py` — `ModuleNotFoundError: No module named 'llm_lab.cfunction'`

- [ ] **Step 3: 구현**

주석 줄은 글자 모양이 아니라 주석·문자열 상태(`_scan`)로 가린다. 그래야 `*`로 시작하지 않는 블록 주석의 가운데 줄도 주석으로 알아보고, 범위가 주석 한가운데서 시작하지 않는다.

````diff
diff --git a/src/llm_lab/cfunction.py b/src/llm_lab/cfunction.py
new file mode 100644
index 0000000..1fcc682
--- /dev/null
+++ b/src/llm_lab/cfunction.py
@@ -0,0 +1,139 @@
+"""C 소스에서 함수 하나의 범위를 찾고, LLM이 낸 함수 코드를 기계로 확인한다 (CodeMetrics).
+
+범위는 함수 바로 위 주석부터 닫는 '}'까지다. 도우미 함수를 그 맨 위에 끼워 넣으므로 범위가
+주석이나 시그니처 한가운데서 시작하면 코드가 깨진다. 그래서 주석 줄은 글자 모양('//', '*'로
+시작하는지)이 아니라 주석·문자열 상태를 따라가며 가린다.
+"""
+
+from __future__ import annotations
+
+import re
+from collections.abc import Iterator
+from dataclasses import dataclass
+
+from llm_lab.fixer import SIGNATURE_LINES, _top_level_blocks, split_lines
+
+
+@dataclass(frozen=True)
+class FunctionRange:
+    start: int  # 바꿀 범위의 첫 줄 (바로 위 주석이 있으면 그 첫 줄, 1부터)
+    signature: int  # 시그니처 첫 줄
+    open_line: int  # 몸통을 여는 '{' 줄
+    end: int  # 닫는 '}' 줄
+
+
+def _scan(text: str) -> Iterator[tuple[int, int, str, bool]]:
+    """주석 밖의 글자를 (위치, 줄 번호, 글자, 문자열·문자 상수 안인지)로 내놓는다.
+
+    주석은 빈칸 하나로 내놓는다(앞뒤 낱말이 붙지 않게). 줄바꿈은 주석 안이어도 내놓는다.
+    """
+    line = 1
+    state = "code"
+    i = 0
+    while i < len(text):
+        ch = text[i]
+        nxt = text[i + 1] if i + 1 < len(text) else ""
+        if ch == "\n":
+            if state == "line_comment":
+                state = "code"
+            yield i, line, ch, state in ("string", "char")
+            line += 1
+        elif state == "code":
+            if ch == "/" and nxt in ("/", "*"):
+                state = "line_comment" if nxt == "/" else "block_comment"
+                yield i, line, " ", False
+                i += 1
+            else:
+                yield i, line, ch, False
+                if ch == '"':
+                    state = "string"
+                elif ch == "'":
+                    state = "char"
+        elif state == "block_comment":
+            if ch == "*" and nxt == "/":
+                state = "code"
+                i += 1
+        elif state in ("string", "char"):
+            yield i, line, ch, True
+            if ch == "\\" and nxt:
+                yield i + 1, line, nxt, True
+                if nxt == "\n":
+                    line += 1
+                i += 1
+            elif (ch == '"' and state == "string") or (ch == "'" and state == "char"):
+                state = "code"
+        i += 1
+
+
+def _code_lines(text: str) -> set[int]:
+    """주석 말고 다른 글자가 있는 줄 번호."""
+    return {line for _, line, ch, _ in _scan(text) if not ch.isspace()}
+
+
+def braces_balanced(code: str) -> bool:
+    """주석·문자열·문자 상수 밖의 중괄호 짝이 맞는지."""
+    depth = 0
+    for _, _, ch, quoted in _scan(code):
+        if quoted or ch not in "{}":
+            continue
+        depth += 1 if ch == "{" else -1
+        if depth < 0:
+            return False
+    return depth == 0
+
+
+def squash_code(code: str) -> str:
+    """비교용: 주석을 빼고, 공백을 한 칸으로 줄이고, 기호 앞뒤 공백을 없앤다."""
+    plain = " ".join("".join(ch for _, _, ch, _ in _scan(code)).split())
+    return re.sub(r" ?([^\w ]) ?", r"\1", plain)
+
+
+def function_name(text: str) -> str:
+    """엑셀 Function 칸 → 함수 이름. 'foo()', ' foo ( void ) ' → 'foo'."""
+    return text.split("(", 1)[0].strip()
+
+
+def function_range(text: str, line: int, name: str) -> FunctionRange | None:
+    """line에 정의된 함수 name의 범위. 못 찾으면 None.
+
+    line은 몸통 안이거나, 몸통을 여는 '{'보다 SIGNATURE_LINES줄 안쪽 위여야 한다.
+    """
+    blocks = _top_level_blocks(text)
+    block = next((b for b in blocks if b[0] <= line <= b[1]), None) or next(
+        (b for b in blocks if line <= b[0] <= line + SIGNATURE_LINES), None
+    )
+    if block is None or not name:
+        return None
+    open_line, end = block
+    lines = split_lines(text)
+    code = _code_lines(text)
+    signature = open_line
+    while signature > 1 and open_line - signature < SIGNATURE_LINES:
+        above = lines[signature - 2].strip()
+        if (
+            not above
+            or above.endswith((";", "}"))
+            or above.startswith("#")
+            or signature - 1 not in code
+        ):
+            break
+        signature -= 1
+    start = signature
+    while start > 1 and lines[start - 2].strip() and start - 1 not in code:
+        start -= 1
+    header = "\n".join(lines[signature - 1 : open_line])
+    if line < start or not re.search(rf"(?<!\w){re.escape(name)}\s*\(", header):
+        return None
+    return FunctionRange(start, signature, open_line, end)
+
+
+def signature_text(text: str, found: FunctionRange) -> str:
+    """시그니처 첫 줄부터 몸통을 여는 '{' 앞까지를 squash_code로 줄인 글."""
+    lines = split_lines(text)
+    offset = sum(len(line) + 1 for line in lines[: found.signature - 1])
+    brace = next(
+        index
+        for index, number, ch, quoted in _scan(text)
+        if index >= offset and number == found.open_line and ch == "{" and not quoted
+    )
+    return squash_code(text[offset:brace])
````

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_cfunction.py -q`
Expected: `9 passed`

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0 (계획 확인 때 `381 passed`), 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/cfunction.py tests/test_cfunction.py
git commit -m "feat: find a C function's range and check refactored code" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: CodeMetrics 시트 읽기 (`kinds/codemetrics.py` 1)

**Files:**
- Create: `src/llm_lab/kinds/codemetrics.py`
- Modify: `src/llm_lab/kinds/__init__.py` (`KINDS`에 `CODEMETRICS`)
- Test: `tests/codemetrics_fixtures.py`(새 파일), `tests/test_codemetrics_sheet.py`(새 파일)

**Interfaces:**
- Consumes: Task 1의 `function_name`. 기반의 `Kind`, `Cell`, `Finding`, `Prepared`, `cell_text`, `data_rows`, `find_header`, `line_number`, `norm`. `conditions.Metric`, `read_metrics(workbook) -> (list[Metric], str)`
- Produces:
  - `CODEMETRICS: Kind`
    - 이름 `codemetrics`, 시트 낱말 `metric`
    - 필수 열 `file, function, line, check, threshold, actual value`
    - `choices = defaults = (FAIL, MIDDLE, UNKNOWN)`
  - 배지 글자 `FAIL = "Fail"`, `MIDDLE = "가운데"`, `UNKNOWN = "단계 모름"`. 뺀 이유는 `NO_FUNCTION`, `PASSED`.
  - 행(`Finding`)
    - `category`: 배지 글자
    - `stage`: 조건 시트 판정 글
    - `threshold`, `actual`: 원문 글자
    - `note`: `값 … · 기준 …`, 그리고 `· 조건 시트: …`나 `· 단계 모름: 이유`
  - 작업 정보 `Prepared.data = {"metrics": [Metric.to_json()…], "pairs": {norm(Check): 지표 번호}, "limits": [[이름, 범위 글]…]}`
  - `metric_words(name) -> frozenset[str]`
  - `pair_metrics(thresholds: dict[str, list[str]], metrics: list[Metric]) -> (dict[str, int], list[str])`
  - `number(text) -> float | None`

- [ ] **Step 1: 실패하는 테스트 쓰기**

시험 데이터는 모두 만들어 낸 값이다. 조건 시트에는 다음을 일부러 넣는다.
- 병합 셀이 남긴 빈 열
- 첫 단계 행에만 있는 지표 이름
- 섞인 범위 글
- 결과 시트와 다른 지표 이름: 낱말로 잇는 것 하나, 값으로 잇는 것 하나

````diff
diff --git a/tests/codemetrics_fixtures.py b/tests/codemetrics_fixtures.py
new file mode 100644
index 0000000..056f82b
--- /dev/null
+++ b/tests/codemetrics_fixtures.py
@@ -0,0 +1,116 @@
+"""CodeMetrics 테스트용 데이터. 값은 모두 만들어 낸 것이다 (회사 엑셀의 값을 넣지 않는다)."""
+
+import json
+
+import httpx2
+from fake_llm import completion
+from rte_fixtures import workbook_bytes
+
+GRADE_PATH = r"C:\proj\src\grade.c"
+METRICS_HEADER = [
+    "File",
+    "Function",
+    "line",
+    "Check",
+    "Threshold",
+    "Actual Value",
+    "Status",
+    "Comment",
+]
+
+# 조건 시트: 제목 행과 빈 행 아래에 헤더가 있고, 병합 셀이 남긴 빈 열이 끼어 있고, 지표 이름은
+# 첫 단계 행에만 있고, 범위 글 모양이 섞여 있다. 지표 이름은 결과 시트와 다르다:
+# "Max. Number of Calling Functions"는 낱말로, "Deepest Nesting"은 값(Pass 끝값 3)으로 잇는다.
+CONDITION_ROWS = [
+    ["코드 지표 기준"],
+    [],
+    ["No", None, "지표", None, "설명", "기준", None, "판정"],
+    [1, None, "Cyclomatic Complexity", None, "분기 수", "1 ~ 10", None, "Pass"],
+    [None, None, None, None, None, "11 ~ 20", None, "검토 후 결정"],
+    [None, None, None, None, None, ">= 21", None, "Fail (고칠 것)"],
+    [2, None, "Deepest Nesting", None, "겹침", "0~3", None, "Pass"],
+    [None, None, None, None, None, ">=4", None, "Fail (고칠 것)"],
+    [3, None, "Max. Number of Calling Functions", None, "부르는 곳", "0 ~ 4", None, "Pass"],
+    [None, None, None, None, None, "5 ~ 8", None, "검토 후 결정"],
+    [None, None, None, None, None, "> 8", None, "Fail (고칠 것)"],
+]
+
+GRADE_C = (
+    b'#include "grade.h"\n'
+    b"\n"
+    b"/* \xc1\xa1\xbc\xf6\xb8\xa6 \xb5\xee\xb1\xde\xc0\xb8\xb7\xce \xb9\xd9\xb2\xdb\xb4\xd9. */\n"
+    b"int grade(int score)\n"
+    b"{\n"
+    b"    int result = 0;\n"
+    b"    if (score > 90) {\n"
+    b"        result = 4;\n"
+    b"    } else if (score > 80) {\n"
+    b"        result = 3;\n"
+    b"    } else {\n"
+    b"        result = 1;\n"
+    b"    }\n"
+    b"    return result;\n"
+    b"}\n"
+)  # CP949로 저장한 파일 ("/* 점수를 등급으로 바꾼다. */")
+
+# LLM이 낼 리팩터링: 도우미를 범위 맨 위에 두고, 원래 주석과 시그니처는 그대로 둔다
+GRADE_REFACTORED = """\
+/* 점수 구간에 맞는 등급. */
+static int grade_band(int score)
+{
+    int band = 1;
+    if (score > 90) {
+        band = 4;
+    } else if (score > 80) {
+        band = 3;
+    } else {
+        band = 1;
+    }
+    return band;
+}
+
+/* 점수를 등급으로 바꾼다. */
+int grade(int score)
+{
+    int result = 0;
+    result = grade_band(score);
+    return result;
+}
+"""
+
+
+def metric_row(
+    check: str = "Cyclomatic Complexity",
+    *,
+    threshold: object = 10,
+    actual: object = 24,
+    function: str = "grade()",
+    line: object = 4,
+    file: str = GRADE_PATH,
+) -> list:
+    return [file, function, line, check, threshold, actual, "Unreviewed", None]
+
+
+def metrics_excel(*rows: list, conditions: list[list] | None = CONDITION_ROWS) -> bytes:
+    """표지, CodeMetrics 결과 시트, (있으면) 조건 시트가 든 엑셀."""
+    sheets: dict[str, list[list]] = {
+        "Cover": [["Polyspace report"]],
+        "CodeMetrics_Result": [METRICS_HEADER, *rows],
+    }
+    if conditions is not None:
+        sheets["Code Metrics"] = conditions
+    return workbook_bytes(sheets)
+
+
+def code_answer(
+    *findings: tuple[str, str], code: str = "", finish_reason: str = "stop"
+) -> httpx2.Response:
+    """CodeMetrics 답. findings는 1번부터 차례로 (판단, 이유)."""
+    body = {
+        "findings": [
+            {"number": number, "decision": decision, "reason": reason}
+            for number, (decision, reason) in enumerate(findings, start=1)
+        ],
+        "code": code,
+    }
+    return completion(json.dumps(body, ensure_ascii=False), finish_reason=finish_reason)
diff --git a/tests/test_codemetrics_sheet.py b/tests/test_codemetrics_sheet.py
new file mode 100644
index 0000000..901d1cc
--- /dev/null
+++ b/tests/test_codemetrics_sheet.py
@@ -0,0 +1,147 @@
+import io
+
+from codemetrics_fixtures import CONDITION_ROWS, metric_row, metrics_excel
+
+from llm_lab.conditions import Metric, read_metrics
+from llm_lab.kinds import KINDS
+from llm_lab.kinds.codemetrics import (
+    CODEMETRICS,
+    FAIL,
+    MIDDLE,
+    NO_FUNCTION,
+    PASSED,
+    UNKNOWN,
+    metric_words,
+    pair_metrics,
+)
+from llm_lab.polyspace import read_sheet
+
+
+def read(data: bytes, selected=(FAIL, MIDDLE, UNKNOWN)):
+    return read_sheet(io.BytesIO(data), CODEMETRICS, selected)
+
+
+ROWS = [
+    metric_row(actual=24),  # 2행: 21 이상 → Fail
+    metric_row(actual=15, function="other()", line=20),  # 3행: 11~20 → 가운데
+    metric_row(actual=8, function="third()", line=30),  # 4행: 1~10 → Pass라 뺀다
+    metric_row("Number of Call Levels", threshold=3, actual=5),  # 5행: 값으로 이음, 4 이상 → Fail
+    metric_row("Number of Calling Functions", threshold=4, actual=6),  # 6행: 낱말로 이음 → 가운데
+    metric_row("Number of Called Functions", threshold=6, actual=9),  # 7행: 조건 시트에 없음
+    metric_row(function="", line=None),  # 8행: 파일 단위 지표
+]
+
+
+def test_targets_get_their_stage_from_the_condition_sheet():
+    sheet = read(metrics_excel(*ROWS))
+
+    assert sheet.sheet == "CodeMetrics_Result"
+    assert [(f.row, f.category, f.stage) for f in sheet.findings] == [
+        (2, FAIL, "Fail (고칠 것)"),
+        (3, MIDDLE, "검토 후 결정"),
+        (5, FAIL, "Fail (고칠 것)"),
+        (6, MIDDLE, "검토 후 결정"),
+        (7, UNKNOWN, ""),
+    ]
+    assert sheet.skipped == {PASSED: 1, NO_FUNCTION: 1}
+    first = sheet.findings[0]
+    assert (first.threshold, first.actual, first.function) == ("10", "24", "grade()")
+    assert first.note == "값 24 · 기준 10 · 조건 시트: Fail (고칠 것)"
+    assert sheet.findings[-1].note == "값 9 · 기준 6 · 단계 모름: 조건 시트에 짝이 없는 지표"
+    assert sheet.prepared.note == (
+        "조건 시트: 지표 3개 · 조건 시트에서 짝을 찾지 못한 지표 1개 · 값으로 이은 지표 1개"
+    )
+
+
+def test_without_a_condition_sheet_every_row_is_a_target_of_unknown_stage():
+    sheet = read(metrics_excel(metric_row(actual=3), conditions=None))
+
+    assert [(f.category, f.note) for f in sheet.findings] == [
+        (UNKNOWN, "값 3 · 기준 10 · 단계 모름: 조건 시트 없음")
+    ]
+    assert sheet.prepared.note == "조건 시트 없음: 결과 시트 Threshold만 씀"
+
+
+def test_values_outside_every_band_or_not_numbers_are_unknown_with_a_reason():
+    conditions = [
+        ["No", "지표", "기준", "판정"],
+        [1, "Cyclomatic Complexity", "1 ~ 10", "Pass"],
+        [None, None, "많음", "Fail"],
+    ]
+    rows = [metric_row(actual=30), metric_row(actual="n/a")]
+    sheet = read(metrics_excel(*rows, conditions=conditions))
+
+    assert [f.note.split("단계 모름: ")[1] for f in sheet.findings] == [
+        "범위 글을 읽지 못함",
+        "Actual Value가 숫자가 아님",
+    ]
+
+
+def test_unselected_stages_are_skipped():
+    sheet = read(metrics_excel(*ROWS), selected=[FAIL])
+
+    assert [f.row for f in sheet.findings] == [2, 5]
+    assert sheet.skipped[f"{MIDDLE} (고르지 않음)"] == 2
+
+
+def test_metric_words_ignore_case_plural_punctuation_and_common_words():
+    assert metric_words("Max. Number of Calling Functions") == {"calling", "function"}
+    assert metric_words("Number of Calling Functions") == metric_words("calling function")
+    assert metric_words("Number of Called Functions") != metric_words("Calling Functions")
+
+
+def bands(*pairs: tuple[str, str]) -> list[list]:
+    rows = [["No", "지표", "기준", "판정"]]
+    for number, (name, edge) in enumerate(pairs, start=1):
+        rows += [[number, name, f"0 ~ {edge}", "Pass"], [None, None, f"> {edge}", "Fail"]]
+    return rows
+
+
+def condition_metrics(rows: list[list]) -> list[Metric]:
+    from openpyxl import load_workbook
+
+    workbook = load_workbook(io.BytesIO(metrics_excel(conditions=rows)), read_only=True)
+    return read_metrics(workbook)[0]
+
+
+def test_pairing_by_value_needs_one_match_in_both_directions():
+    metrics = condition_metrics(bands(("Nesting A", "3"), ("Nesting B", "5")))
+
+    pairs, by_value = pair_metrics({"Level One": ["3"], "Level Two": ["5"]}, metrics)
+    assert pairs == {"level one": 0, "level two": 1}
+    assert by_value == ["Level One", "Level Two"]
+
+    # 같은 한도의 Check가 둘이면 어느 쪽인지 모르므로 잇지 않는다
+    pairs, _ = pair_metrics({"Level One": ["3"], "Level Two": ["3"]}, metrics)
+    assert pairs == {}
+    # 한도가 두 가지인 Check도 잇지 않는다
+    assert pair_metrics({"Level One": ["3", "5"]}, metrics)[0] == {}
+
+
+def test_limits_name_both_sheets_and_keep_thresholds_without_a_pair():
+    sheet = read(metrics_excel(*ROWS))
+
+    assert sheet.prepared.data["limits"] == [
+        ["Cyclomatic Complexity", "Pass range 1 ~ 10"],
+        ["Number of Call Levels (Deepest Nesting)", "Pass range 0~3"],
+        ["Number of Calling Functions (Max. Number of Calling Functions)", "Pass range 0 ~ 4"],
+        ["Number of Called Functions", "Threshold 6"],
+    ]
+
+
+def test_kind_groups_by_function_and_leaves_middle_rows_for_people():
+    fail, middle = read(metrics_excel(*ROWS)).findings[:2]
+
+    assert KINDS["codemetrics"] is CODEMETRICS
+    assert CODEMETRICS.group_key(fail, "src/grade.c") == "src/grade.c:grade"
+    assert (CODEMETRICS.label(fail), CODEMETRICS.label(middle)) == (FAIL, MIDDLE)
+    assert (CODEMETRICS.auto_select(fail), CODEMETRICS.auto_select(middle)) == (True, False)
+    assert CODEMETRICS.rank(FAIL) < CODEMETRICS.rank(MIDDLE) < CODEMETRICS.rank(UNKNOWN)
+
+
+def test_condition_rows_fixture_is_read_as_three_metrics():
+    assert [m.name for m in condition_metrics(CONDITION_ROWS)] == [
+        "Cyclomatic Complexity",
+        "Deepest Nesting",
+        "Max. Number of Calling Functions",
+    ]
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_codemetrics_sheet.py -q`
Expected: `ERROR tests/test_codemetrics_sheet.py` — `ModuleNotFoundError: No module named 'llm_lab.kinds.codemetrics'`

- [ ] **Step 3: 구현**

`SYSTEM_PROMPT`는 여기서 역할 문장만 두고, Task 3에서 전문으로 채운다.

````diff
diff --git a/src/llm_lab/kinds/__init__.py b/src/llm_lab/kinds/__init__.py
index 0f9bda4..6127c81 100644
--- a/src/llm_lab/kinds/__init__.py
+++ b/src/llm_lab/kinds/__init__.py
@@ -1,9 +1,10 @@
 """Polyspace 결과 시트 종류. 새 종류는 이 패키지에 파일 하나를 더하고 KINDS에 넣는다."""
 
 from llm_lab.kinds.base import Cell, Kind
+from llm_lab.kinds.codemetrics import CODEMETRICS
 from llm_lab.kinds.misra import MISRA
 from llm_lab.kinds.rte import RTE
 
-KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE, MISRA)}
+KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE, MISRA, CODEMETRICS)}
 
-__all__ = ["KINDS", "MISRA", "RTE", "Cell", "Kind"]
+__all__ = ["CODEMETRICS", "KINDS", "MISRA", "RTE", "Cell", "Kind"]
diff --git a/src/llm_lab/kinds/codemetrics.py b/src/llm_lab/kinds/codemetrics.py
new file mode 100644
index 0000000..6434e41
--- /dev/null
+++ b/src/llm_lab/kinds/codemetrics.py
@@ -0,0 +1,242 @@
+"""CodeMetrics 결과 시트: 함수의 측정값이 한도를 넘은 행을 리팩터링으로 고친다.
+
+지표 목록, 한도, 단계는 회사 양식이라 바뀔 수 있으므로 작업마다 엑셀(결과 시트와 조건 시트)에서
+읽고 코드와 지시문에 고정하지 않는다. "클수록 나쁨" 같은 방향도 가정하지 않고 조건 시트의 범위로
+단계를 정한다. 결과 시트에 올라온 행은 Polyspace가 한도를 넘었다고 본 행이다.
+"""
+
+from __future__ import annotations
+
+import re
+from typing import Any
+
+from llm_lab.cfunction import function_name
+from llm_lab.conditions import Metric, read_metrics
+from llm_lab.kinds.base import Cell, Kind
+from llm_lab.polyspace import (
+    Finding,
+    Prepared,
+    cell_text,
+    data_rows,
+    find_header,
+    line_number,
+    norm,
+)
+
+REQUIRED = ("file", "function", "line", "check", "threshold", "actual value")
+# 배지 글자 (이 도구가 정한 이름). 조건 시트의 판정 글은 카드 메모에 그대로 보인다
+FAIL, MIDDLE, UNKNOWN = "Fail", "가운데", "단계 모름"
+NO_FUNCTION = "파일 단위 지표 (Function 없음)"
+PASSED = "조건 시트상 Pass"
+# 지표 이름을 낱말로 비교할 때 빼는 낱말
+STOP_WORDS = frozenset({"number", "of", "max", "maximum", "the"})
+
+SYSTEM_PROMPT = """\
+You refactor one C function so that its Polyspace code metrics fall within the project's \
+limits, without changing what the function does.
+"""
+
+
+def metric_words(name: str) -> frozenset[str]:
+    """지표 이름 → 비교용 낱말 집합. 문장 부호, 복수 s, 흔한 낱말(number, of, max…)을 뺀다."""
+    words = re.findall(r"[a-z0-9]+", name.casefold())
+    return (
+        frozenset(word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words)
+        - STOP_WORDS
+    )
+
+
+def number(text: str) -> float | None:
+    try:
+        return float(text.replace(",", ""))
+    except ValueError:
+        return None
+
+
+def _result_thresholds(workbook: Any) -> dict[str, list[str]]:
+    """결과 시트의 Check → 그 Check에 나온 Threshold 글 (처음 나온 순서, 중복 없이).
+
+    read_sheet와 같은 규칙으로 결과 시트를 고른다: 이름이 _Result로 끝나고 metric이 들어간 시트.
+    """
+    for sheet in workbook.worksheets:
+        title = sheet.title.casefold()
+        if not title.endswith("_result") or "metric" not in title:
+            continue
+        found = find_header(sheet, REQUIRED)
+        if isinstance(found, str):
+            continue
+        thresholds: dict[str, list[str]] = {}
+        for _, cell in data_rows(sheet, *found):
+            check, text = cell_text(cell("check")), cell_text(cell("threshold"))
+            values = thresholds.setdefault(check, [])
+            if text and text not in values:
+                values.append(text)
+        thresholds.pop("", None)
+        return thresholds
+    return {}
+
+
+def _edge(metric: Metric) -> float | None:
+    """Pass 범위의 끝값: 위 끝이 있으면 위 끝, 없으면 아래 끝 (이상이어야 하는 지표)."""
+    band = metric.pass_band
+    if band is None:
+        return None
+    return band.high if band.high is not None else band.low
+
+
+def pair_metrics(
+    thresholds: dict[str, list[str]], metrics: list[Metric]
+) -> tuple[dict[str, int], list[str]]:
+    """결과 시트 Check(norm) → 조건 시트 지표 번호와, 값으로 이은 Check 목록.
+
+    낱말 집합이 같으면 잇는다. 남은 것끼리는 Check의 Threshold가 한 가지이고, 그 값과 Pass 범위의
+    끝값이 같은 지표가 서로 하나뿐일 때만 잇는다.
+    """
+    pairs: dict[str, int] = {}
+    for check in thresholds:
+        hits = [i for i, m in enumerate(metrics) if metric_words(m.name) == metric_words(check)]
+        if len(hits) == 1:
+            pairs[norm(check)] = hits[0]
+    used = set(pairs.values())
+    single: dict[str, float] = {}
+    for check, texts in thresholds.items():
+        values = {number(text) for text in texts}
+        if norm(check) not in pairs and len(values) == 1 and None not in values:
+            single[check] = values.pop()  # type: ignore[assignment]
+    by_value: list[str] = []
+    for check, value in single.items():
+        hits = [i for i, m in enumerate(metrics) if i not in used and _edge(m) == value]
+        rivals = [other for other, limit in single.items() if limit == value]
+        if len(hits) == 1 and len(rivals) == 1:
+            pairs[norm(check)] = hits[0]
+            by_value.append(check)
+    return pairs, by_value
+
+
+def _limits(
+    metrics: list[Metric], thresholds: dict[str, list[str]], pairs: dict[str, int]
+) -> list[list[str]]:
+    """LLM에 줄 (지표 이름, 지켜야 할 범위). 조건 시트의 모든 지표와, 짝이 없는 결과 시트 Check."""
+    checks: dict[int, list[str]] = {}
+    for check in thresholds:
+        index = pairs.get(norm(check))
+        if index is not None:
+            checks.setdefault(index, []).append(check)
+    limits = []
+    for index, metric in enumerate(metrics):
+        band = metric.pass_band
+        if band is None:
+            continue
+        others = [check for check in checks.get(index, []) if check != metric.name]
+        title = f"{', '.join(others)} ({metric.name})" if others else metric.name
+        limits.append([title, f"Pass range {band.text}"])
+    for check, texts in thresholds.items():
+        if norm(check) not in pairs:
+            limits.append([check, "Threshold " + ", ".join(texts)])
+    return limits
+
+
+def prepare(workbook: Any) -> Prepared:
+    """조건 시트의 단계, 결과 시트 Check와의 짝, LLM에 줄 한도 표를 작업 정보로 만든다."""
+    metrics, note = read_metrics(workbook)
+    thresholds = _result_thresholds(workbook)
+    pairs, by_value = pair_metrics(thresholds, metrics)
+    notes = [note]
+    unpaired = [check for check in thresholds if norm(check) not in pairs]
+    if metrics and unpaired:
+        notes.append(f"조건 시트에서 짝을 찾지 못한 지표 {len(unpaired)}개")
+    if by_value:
+        notes.append(f"값으로 이은 지표 {len(by_value)}개")
+    data = {
+        "metrics": [metric.to_json() for metric in metrics],
+        "pairs": pairs,
+        "limits": _limits(metrics, thresholds, pairs),
+    }
+    return Prepared(data, " · ".join(notes))
+
+
+def _stage(context: Any, check: str, actual: str) -> tuple[str, str, str]:
+    """(배지 글자, 조건 시트 판정 글, 단계 모름 이유). Pass면 배지 글자가 'pass'다."""
+    data = context or {}
+    metrics = data.get("metrics") or []
+    if not metrics:
+        return UNKNOWN, "", "조건 시트 없음"
+    index = (data.get("pairs") or {}).get(norm(check))
+    if index is None:
+        return UNKNOWN, "", "조건 시트에 짝이 없는 지표"
+    value = number(actual)
+    if value is None:
+        return UNKNOWN, "", "Actual Value가 숫자가 아님"
+    metric = Metric.from_json(metrics[index])
+    band = metric.stage(value)
+    if band is None:
+        unread = any(not other.ok for other in metric.bands)
+        return UNKNOWN, "", "범위 글을 읽지 못함" if unread else "어느 범위에도 들지 않음"
+    return {"pass": "pass", "fail": FAIL}.get(band.kind, MIDDLE), band.verdict, ""
+
+
+def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
+    """Function이 있고 조건 시트상 Pass가 아닌 행이 대상이다. 단계는 category에 담는다."""
+    function = cell_text(cell("function"))
+    if not function_name(function):
+        return NO_FUNCTION
+    check = cell_text(cell("check"))
+    threshold, actual = cell_text(cell("threshold")), cell_text(cell("actual value"))
+    label, verdict, unknown = _stage(context, check, actual)
+    if label == "pass":
+        return PASSED
+    note = f"값 {actual} · 기준 {threshold}"
+    if verdict:
+        note += f" · 조건 시트: {verdict}"
+    if unknown:
+        note += f" · 단계 모름: {unknown}"
+    return Finding(
+        row=row,
+        color="",
+        type="",
+        file=cell_text(cell("file")),
+        line=line_number(cell("line")),
+        check=check,
+        detail="",
+        function=function,
+        status=cell_text(cell("status")),
+        comment=cell_text(cell("comment")),
+        category=label,
+        note=note,
+        threshold=threshold,
+        actual=actual,
+        stage=verdict,
+    )
+
+
+def stage_of(finding: Finding) -> str:
+    return finding.category or UNKNOWN
+
+
+def same_function(finding: Finding, file_name: str) -> str:
+    """같은 파일의 같은 함수에 걸린 행끼리 묶는다 (함수 하나에 LLM 한 번)."""
+    return f"{file_name}:{function_name(finding.function)}"
+
+
+def not_middle(finding: Finding) -> bool:
+    """가운데 단계 행은 사람이 검토해서 고른다."""
+    return finding.category != MIDDLE
+
+
+CODEMETRICS = Kind(
+    name="codemetrics",
+    title="CodeMetrics",
+    sheet_word="metric",
+    required=REQUIRED,
+    read_row=read_row,
+    label=stage_of,
+    choices=(FAIL, MIDDLE, UNKNOWN),
+    defaults=(FAIL, MIDDLE, UNKNOWN),
+    colors={FAIL: "red", MIDDLE: "orange", UNKNOWN: "gray"},
+    system_prompt=SYSTEM_PROMPT,
+    heading="Polyspace code metrics over their limits in one C function",
+    prompt_version=1,
+    prepare=prepare,
+    group_key=same_function,
+    auto_select=not_middle,
+)
````

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_codemetrics_sheet.py -q`
Expected: `9 passed`

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0 (계획 확인 때 `390 passed`), 린트 통과. 첫 화면 종류 목록 테스트 등 기존 테스트도 그대로 통과해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/kinds/codemetrics.py src/llm_lab/kinds/__init__.py tests/codemetrics_fixtures.py tests/test_codemetrics_sheet.py
git commit -m "feat: read CodeMetrics rows with bands from the condition sheet" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: LLM 메시지와 보낼 범위 (`kinds/codemetrics.py` 2)

**Files:**
- Modify: `src/llm_lab/kinds/codemetrics.py`
- Test: `tests/test_codemetrics_prompt.py`(새 파일)

**Interfaces:**
- Consumes: Task 1의 `FunctionRange`, `function_range`. Task 2의 `metric_words`와 작업 정보의 `limits`. 기반의 `fixer.WHOLE_FILE_MAX_LINES`, `FixError`, `split_lines`, `user_error`, `SourceFile`
- Produces:
  - `SYSTEM_PROMPT` 전문
  - `FUNCTION_MAX_LINES = 1000`
  - `CALLING = metric_words("Number of Calling Functions")`
  - `TIPS: dict[frozenset[str], str]`
  - `facts(finding)`
  - `_function(text, findings) -> FunctionRange`: 못 찾거나 너무 길면 `FixError`
  - `region(text, findings) -> (first, last)`
  - `prompt_tail(context, findings, source) -> str`
  - `CODEMETRICS`에 `facts`, `prompt_tail`, `region`을 더한다.

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_codemetrics_prompt.py b/tests/test_codemetrics_prompt.py
new file mode 100644
index 0000000..1e1a327
--- /dev/null
+++ b/tests/test_codemetrics_prompt.py
@@ -0,0 +1,86 @@
+import io
+
+import pytest
+from codemetrics_fixtures import GRADE_C, metric_row, metrics_excel
+
+from llm_lab.fixer import FixError, make_request
+from llm_lab.kinds import codemetrics
+from llm_lab.kinds.codemetrics import CODEMETRICS
+from llm_lab.polyspace import read_sheet
+from llm_lab.sources import decode_source
+
+SOURCE = decode_source("src/grade.c", GRADE_C)
+
+
+def job(*rows: list):
+    """(엑셀에서 읽은 대상 행들, 작업 정보)."""
+    sheet = read_sheet(io.BytesIO(metrics_excel(*rows)), CODEMETRICS, CODEMETRICS.defaults)
+    return sheet.findings, sheet.prepared.data
+
+
+def test_message_lists_values_limits_tips_and_the_lines_to_replace():
+    findings, context = job(
+        metric_row(actual=24),
+        metric_row("Number of Calling Functions", threshold=4, actual=6),
+        metric_row("Number of Called Functions", threshold=6, actual=9, function="other()"),
+    )
+
+    request = make_request(CODEMETRICS, findings[:2], SOURCE, context)
+    system, user = (message["content"] for message in request.messages)
+
+    assert system == CODEMETRICS.system_prompt
+    assert "Answer with JSON only" in system
+    assert user.startswith("Polyspace code metrics over their limits in one C function:")
+    assert (
+        "Finding 1:\n- check: Cyclomatic Complexity\n- Actual Value: 24\n- Threshold: 10\n"
+        "- stage: Fail (고칠 것)\n- Function: grade()\n- File: src/grade.c\n- line: 4"
+    ) in user
+    # 한도 표에는 조건 시트의 모든 지표와 짝 없는 Check가 들어간다
+    assert "- Cyclomatic Complexity: Pass range 1 ~ 10" in user
+    assert "- Number of Called Functions: Threshold 6" in user
+    # 요령은 이 묶음의 지표만
+    assert "Cyclomatic Complexity counts decision points" in user
+    assert "Number of Calling Functions counts the distinct callers" in user
+    assert "Number of Called Functions counts" not in user
+    assert "Replace lines 3-15 of src/grade.c (function grade and the comment" in user
+    assert "Code (lines 1-15 of src/grade.c;" in user  # 파일이 짧아 파일 전체를 보인다
+    assert (request.first, request.last) == (1, 15)
+
+
+def test_long_files_show_only_the_function(monkeypatch):
+    monkeypatch.setattr(codemetrics, "WHOLE_FILE_MAX_LINES", 10)
+    findings, context = job(metric_row())
+
+    request = make_request(CODEMETRICS, findings, SOURCE, context)
+
+    assert (request.first, request.last) == (3, 15)
+
+
+def test_function_not_in_the_source_is_a_row_error():
+    findings, context = job(metric_row(function="missing()"))
+
+    with pytest.raises(FixError) as caught:
+        make_request(CODEMETRICS, findings, SOURCE, context)
+
+    assert str(caught.value).startswith("엑셀의 함수를 소스에서 찾지 못했습니다.")
+
+
+def test_very_long_function_is_not_sent(monkeypatch):
+    monkeypatch.setattr(codemetrics, "FUNCTION_MAX_LINES", 12)
+    findings, context = job(metric_row())
+
+    with pytest.raises(FixError) as caught:
+        make_request(CODEMETRICS, findings, SOURCE, context)
+
+    message = str(caught.value)
+    assert message.startswith("함수가 너무 길어 자동으로 나누지 않았습니다.")
+    assert "13줄 (기준 12줄)" in message
+
+
+def test_limits_come_from_the_job_not_from_code():
+    excel = metrics_excel(metric_row(threshold=99, actual=100), conditions=None)
+    sheet = read_sheet(io.BytesIO(excel), CODEMETRICS, ["단계 모름"])
+
+    request = make_request(CODEMETRICS, sheet.findings, SOURCE, sheet.prepared.data)
+
+    assert "- Cyclomatic Complexity: Threshold 99" in request.messages[1]["content"]
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_codemetrics_prompt.py -q`
Expected: `5 failed`. 메시지에 값·한도·요령·바꿀 줄이 없고, `codemetrics`에 `WHOLE_FILE_MAX_LINES`·`FUNCTION_MAX_LINES`가 없다.

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/kinds/codemetrics.py b/src/llm_lab/kinds/codemetrics.py
index 6434e41..9140d4b 100644
--- a/src/llm_lab/kinds/codemetrics.py
+++ b/src/llm_lab/kinds/codemetrics.py
@@ -10,8 +10,9 @@ from __future__ import annotations
 import re
 from typing import Any
 
-from llm_lab.cfunction import function_name
+from llm_lab.cfunction import FunctionRange, function_name, function_range
 from llm_lab.conditions import Metric, read_metrics
+from llm_lab.fixer import WHOLE_FILE_MAX_LINES, FixError, split_lines, user_error
 from llm_lab.kinds.base import Cell, Kind
 from llm_lab.polyspace import (
     Finding,
@@ -22,6 +23,7 @@ from llm_lab.polyspace import (
     line_number,
     norm,
 )
+from llm_lab.sources import SourceFile
 
 REQUIRED = ("file", "function", "line", "check", "threshold", "actual value")
 # 배지 글자 (이 도구가 정한 이름). 조건 시트의 판정 글은 카드 메모에 그대로 보인다
@@ -31,9 +33,40 @@ PASSED = "조건 시트상 Pass"
 # 지표 이름을 낱말로 비교할 때 빼는 낱말
 STOP_WORDS = frozenset({"number", "of", "max", "maximum", "the"})
 
+# 함수를 통째로 다시 써야 하므로 이보다 긴 함수는 LLM에 보내지 않는다 (답이 잘린다)
+FUNCTION_MAX_LINES = 1000
+
 SYSTEM_PROMPT = """\
 You refactor one C function so that its Polyspace code metrics fall within the project's \
 limits, without changing what the function does.
+Scope:
+- Answer with "code": the complete new text for the line range named in the request \
+(the function and the comment right above it). Lines outside that range stay as they are.
+- Keep the function's name, parameters, return type and specifiers (such as static) \
+exactly as they are, and keep the comment above it.
+- You may add new static helper functions at the top of "code", above that comment. \
+Give each a one-line comment and a name of at most 31 characters \
+that is not used elsewhere in the file.
+- Do not add global variables. Do not change other functions, other files or headers.
+Keep the behavior:
+- Move statements mechanically. Keep the order of evaluation, side effects, calls \
+and accesses to globals and volatile objects. Keep every original comment.
+- Pass the values a helper needs as parameters, and return changed values \
+through pointers or the return value.
+- Do not add early returns, goto, recursion or dynamic memory. New code must follow \
+MISRA C:2012 (for example Rule 15.5 single point of exit and Rule 8.8 static).
+- Every function in "code", including new helpers, must stay within all the limits given.
+Decide for each numbered finding:
+- fix: your code brings that metric within its limit.
+- no_fix: it cannot be done within these rules, or the change would put the behavior \
+at risk. Number of Calling Functions is always no_fix, because only its callers could change it.
+Do not guess definitions (macros, types, other functions) that are not shown. \
+If they matter, say so in reason.
+Write each "reason" in Korean, one to three sentences that a reviewer can paste \
+into the Polyspace comment of that finding. For fix, say what moved where and \
+the rough new value. For no_fix, say why.
+Answer with JSON only. "findings" has one entry for each numbered finding. \
+"code" is "" when no finding is fix.
 """
 
 
@@ -46,6 +79,33 @@ def metric_words(name: str) -> frozenset[str]:
     )
 
 
+CALLING = metric_words("Number of Calling Functions")
+# Polyspace 지표 뜻(MathWorks 문서)에 따른 줄이는 요령. 회사 조건이 아니라 공개된 지식이다.
+# 이 묶음에 나온 지표의 요령만 메시지에 붙인다.
+TIPS = {
+    metric_words("Cyclomatic Complexity"): (
+        "Cyclomatic Complexity counts decision points plus one: each if, else if, for, while "
+        "and ?:, and each case followed by break. && and || do not count. Move cohesive "
+        "branches or case bodies into static helpers, or merge a nested if without else into "
+        "one condition with &&."
+    ),
+    metric_words("Number of Called Functions"): (
+        "Number of Called Functions counts the distinct functions called in the body, "
+        "standard library functions included. Calls through function pointers and assert do "
+        "not count. Group calls that belong together into a static helper."
+    ),
+    metric_words("Number of Call Levels"): (
+        "Number of Call Levels is the deepest nesting of if, switch, for and while, not the "
+        "call depth. Each else if adds a level. Move inner blocks into static helpers, or turn "
+        "a long else-if chain on one value into a switch."
+    ),
+    CALLING: (
+        "Number of Calling Functions counts the distinct callers. Changing callers is outside "
+        "this task, so answer no_fix and say why many callers are expected for this function."
+    ),
+}
+
+
 def number(text: str) -> float | None:
     try:
         return float(text.replace(",", ""))
@@ -209,6 +269,73 @@ def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
     )
 
 
+def facts(finding: Finding) -> list[tuple[str, str]]:
+    """사용자 메시지에 넣을 값. File과 line은 fixer가 붙인다."""
+    return [
+        ("check", finding.check),
+        ("Actual Value", finding.actual),
+        ("Threshold", finding.threshold),
+        ("stage", finding.stage),
+        ("Function", finding.function),
+    ]
+
+
+def _function(text: str, findings: list[Finding]) -> FunctionRange:
+    """묶음 함수의 바꿀 범위. 못 찾거나 너무 길면 FixError다 (그 묶음만 오류)."""
+    first = findings[0]
+    name = function_name(first.function)
+    assert first.line is not None  # make_request가 먼저 확인한다
+    found = function_range(text, first.line, name)
+    if found is None:
+        raise FixError(
+            user_error(
+                "엑셀의 함수를 소스에서 찾지 못했습니다.",
+                "엑셀을 만든 것과 같은 버전의 소스 폴더를 골라 다시 분석하세요.",
+                f"{name}, {first.line}번 줄",
+            )
+        )
+    size = found.end - found.start + 1
+    if size > FUNCTION_MAX_LINES:
+        raise FixError(
+            user_error(
+                "함수가 너무 길어 자동으로 나누지 않았습니다.",
+                "직접 나누거나 Polyspace comment에 사유를 적으세요.",
+                f"{size}줄 (기준 {FUNCTION_MAX_LINES}줄)",
+            )
+        )
+    return found
+
+
+def region(text: str, findings: list[Finding]) -> tuple[int, int]:
+    """파일이 WHOLE_FILE_MAX_LINES줄 이하면 파일 전체를, 넘으면 바꿀 범위만 보인다."""
+    found = _function(text, findings)
+    total = len(split_lines(text))
+    return (1, total) if total <= WHOLE_FILE_MAX_LINES else (found.start, found.end)
+
+
+def prompt_tail(context: Any, findings: list[Finding], source: SourceFile) -> str:
+    """한도 표, 이 묶음 지표의 요령, 바꿀 줄 범위."""
+    parts = []
+    limits = (context or {}).get("limits") or []
+    if limits:
+        listed = "\n".join(f"- {name}: {limit}" for name, limit in limits)
+        parts.append(
+            "Limits every function must meet, including new helpers "
+            f"(read from this project's workbook):\n{listed}"
+        )
+    present = {metric_words(finding.check) for finding in findings}
+    tips = [tip for words, tip in TIPS.items() if words in present]
+    if tips:
+        parts.append("About these metrics:\n" + "\n".join(f"- {tip}" for tip in tips))
+    found = _function(source.text, findings)
+    name = function_name(findings[0].function)
+    parts.append(
+        f"Replace lines {found.start}-{found.end} of {source.name} (function {name} and the "
+        'comment right above it) with "code". Every other line stays as it is.'
+    )
+    return "\n\n".join(parts)
+
+
 def stage_of(finding: Finding) -> str:
     return finding.category or UNKNOWN
 
@@ -238,5 +365,8 @@ CODEMETRICS = Kind(
     prompt_version=1,
     prepare=prepare,
     group_key=same_function,
+    facts=facts,
+    prompt_tail=prompt_tail,
+    region=region,
     auto_select=not_middle,
 )
````

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_codemetrics_prompt.py -q`
Expected: `5 passed`

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0 (계획 확인 때 `395 passed`), 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/kinds/codemetrics.py tests/test_codemetrics_prompt.py
git commit -m "feat: ask the LLM to rewrite one function within the workbook limits" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 답 처리 (`kinds/codemetrics.py` 3)

**Files:**
- Modify: `src/llm_lab/kinds/codemetrics.py`
- Test: `tests/test_codemetrics_answer.py`(새 파일)

**Interfaces:**
- Consumes:
  - Task 1의 `braces_balanced`, `signature_text`, `squash_code`
  - Task 3의 `_function`, `CALLING`
  - 기반의 `FIX_SCHEMA`, `NUMBER_PREFIX`, `Edit`, `EditMismatch(detail, kind, summary)`, `FixResult`, `Request`, `_minimize`, `apply_edits`, `display_diff`, `read_decisions`, `share`
- Produces:
  - `CODE_SCHEMA`: `findings`는 공통 형식과 같고, `edits` 대신 `code`
  - `to_results(data, request) -> list[FixResult]`
  - `TIMEOUT = 300.0`
  - 설명 문구 `CALLING_FIX`, `SIGNATURE_CHANGED`, `COMMENT_DROPPED`, `BRACES`
  - `CODEMETRICS`에 `answer_schema`, `to_results`, `timeout`을 더한다.

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_codemetrics_answer.py b/tests/test_codemetrics_answer.py
new file mode 100644
index 0000000..f647082
--- /dev/null
+++ b/tests/test_codemetrics_answer.py
@@ -0,0 +1,152 @@
+import io
+
+import pytest
+from codemetrics_fixtures import GRADE_C, GRADE_REFACTORED, code_answer, metric_row, metrics_excel
+from fake_llm import MODEL, make_client, scripted
+
+from llm_lab.fixer import FixError, FixResult, apply_edits, make_request, propose_fixes
+from llm_lab.kinds.codemetrics import (
+    BRACES,
+    CALLING_FIX,
+    CODE_SCHEMA,
+    CODEMETRICS,
+    COMMENT_DROPPED,
+    SIGNATURE_CHANGED,
+)
+from llm_lab.polyspace import read_sheet
+from llm_lab.sources import decode_source
+
+SOURCE = decode_source("src/grade.c", GRADE_C)
+FIXED = '#include "grade.h"\n\n' + GRADE_REFACTORED  # 범위(3~15줄)를 code로 바꾼 파일
+GROUP = (
+    metric_row(actual=24),
+    metric_row("Number of Calling Functions", threshold=4, actual=6),
+)
+
+
+def request_for(*rows: list):
+    sheet = read_sheet(io.BytesIO(metrics_excel(*rows)), CODEMETRICS, CODEMETRICS.defaults)
+    return make_request(CODEMETRICS, sheet.findings, SOURCE, sheet.prepared.data)
+
+
+def ask(request, *responses):
+    handler, sent = scripted(*responses)
+    return propose_fixes(make_client(handler), MODEL, request), sent
+
+
+def test_fix_rows_share_the_refactoring_and_calling_rows_keep_only_a_reason():
+    results, sent = ask(
+        request_for(*GROUP),
+        code_answer(
+            ("fix", "등급 계산을 도우미로 옮김"), ("no_fix", "공용 함수"), code=GRADE_REFACTORED
+        ),
+    )
+
+    assert sent[0]["response_format"]["json_schema"]["schema"] == CODE_SCHEMA
+    fix, calling = results
+    assert (fix.decision, fix.reason) == ("fix", "등급 계산을 도우미로 옮김")
+    assert apply_edits(SOURCE.text, fix.edits) == FIXED
+    assert calling == FixResult("no_fix", "공용 함수", (), "")
+    # 바뀐 줄만 남는다: 원래 주석과 시그니처는 diff에서 빠지지 않는다
+    assert "+static int grade_band(int score)" in fix.diff
+    assert "-/* 점수를 등급으로 바꾼다. */" not in fix.diff
+    assert "-int grade(int score)" not in fix.diff
+
+
+def test_when_nothing_is_fixed_the_code_is_ignored():
+    results, _ = ask(request_for(*GROUP), code_answer(("no_fix", "a"), ("no_fix", "b"), code="{"))
+
+    assert results == [FixResult("no_fix", "a", (), ""), FixResult("no_fix", "b", (), "")]
+
+
+def test_fixing_the_calling_functions_row_is_refused_and_asked_again():
+    request = request_for(*GROUP)
+    wrong = code_answer(("fix", "x"), ("fix", "y"), code=GRADE_REFACTORED)
+    right = code_answer(("fix", "x"), ("no_fix", "y"), code=GRADE_REFACTORED)
+
+    results, sent = ask(request, wrong, right)
+
+    assert [result.decision for result in results] == ["fix", "no_fix"]
+    assert (
+        "Finding 2은 Number of Calling Functions라 no_fix여야 함"
+        in sent[1]["messages"][-1]["content"]
+    )
+
+
+@pytest.mark.parametrize(
+    ("code", "summary"),
+    [
+        (
+            GRADE_REFACTORED.replace("int grade(int score)", "int grade(long score)"),
+            SIGNATURE_CHANGED,
+        ),
+        (GRADE_REFACTORED.replace("/* 점수를 등급으로 바꾼다. */\n", ""), COMMENT_DROPPED),
+        (GRADE_REFACTORED + "}\n", BRACES),
+    ],
+)
+def test_broken_code_is_asked_again_and_then_explained(code, summary):
+    bad = code_answer(("fix", "x"), code=code)
+
+    with pytest.raises(FixError) as caught:
+        ask(request_for(metric_row()), bad, bad)
+
+    assert str(caught.value).splitlines()[0] == summary
+
+
+def test_calling_fix_twice_is_explained():
+    wrong = code_answer(("fix", "x"), ("fix", "y"), code=GRADE_REFACTORED)
+
+    with pytest.raises(FixError) as caught:
+        ask(request_for(*GROUP), wrong, wrong)
+
+    assert str(caught.value).splitlines()[0] == CALLING_FIX
+
+
+def test_line_number_prefixes_copied_from_the_shown_code_are_removed():
+    numbered = "\n".join(
+        f"{number:>5}| {line}" for number, line in enumerate(GRADE_REFACTORED.splitlines(), 3)
+    )
+
+    results, _ = ask(request_for(metric_row()), code_answer(("fix", "x"), code=numbered))
+
+    assert apply_edits(SOURCE.text, results[0].edits) == FIXED
+
+
+def test_characters_the_file_encoding_cannot_hold_are_asked_again():
+    emoji = GRADE_REFACTORED.replace("등급.", "등급 \U0001f600.")
+    request = request_for(metric_row())
+
+    results, sent = ask(
+        request,
+        code_answer(("fix", "x"), code=emoji),
+        code_answer(("fix", "x"), code=GRADE_REFACTORED),
+    )
+
+    assert "U+1F600" in sent[1]["messages"][-1]["content"]
+    assert apply_edits(SOURCE.text, results[0].edits) == FIXED
+
+
+def test_missing_numbers_unchanged_code_and_cut_answers_are_asked_again():
+    request = request_for(*GROUP)
+    original = SOURCE.text.split("\n", 2)[2]  # 3줄부터 끝까지 = 바꿀 범위 그대로
+
+    _, sent = ask(
+        request,
+        code_answer(("fix", "x"), code=GRADE_REFACTORED),  # 2번이 빠짐
+        code_answer(("fix", "x"), ("no_fix", "y"), code=GRADE_REFACTORED),
+    )
+    assert "findings의 number는 1부터 2까지" in sent[1]["messages"][-1]["content"]
+
+    with pytest.raises(FixError) as caught:
+        same = code_answer(("fix", "x"), ("no_fix", "y"), code=original)
+        ask(request, same, same)
+    assert "수정해도 원본과 같음" in str(caught.value)
+
+    with pytest.raises(FixError) as caught:
+        cut = code_answer(("fix", "x"), ("no_fix", "y"), code="", finish_reason="length")
+        ask(request, cut, cut)
+    assert str(caught.value).startswith("LLM 답이 길이 제한에 걸려 잘렸습니다.")
+
+
+def test_kind_waits_longer_than_rte():
+    assert CODEMETRICS.timeout == 300.0
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_codemetrics_answer.py -q`
Expected: `ERROR tests/test_codemetrics_answer.py` — `ImportError: cannot import name 'BRACES'`

- [ ] **Step 3: 구현**

검사 순서는 다음과 같다. 어느 하나라도 실패하면 기반의 공통 코드가 한 번 다시 묻는다.
1. 번호(공통)
2. Calling Functions 행이 `fix`인지
3. `code`가 비었는지
4. 줄 번호 표시를 떼기
5. 인코딩
6. 중괄호
7. 시그니처
8. 위 주석
9. 원본과 같은지

````diff
diff --git a/src/llm_lab/kinds/codemetrics.py b/src/llm_lab/kinds/codemetrics.py
index 9140d4b..ed47f2b 100644
--- a/src/llm_lab/kinds/codemetrics.py
+++ b/src/llm_lab/kinds/codemetrics.py
@@ -10,9 +10,32 @@ from __future__ import annotations
 import re
 from typing import Any
 
-from llm_lab.cfunction import FunctionRange, function_name, function_range
+from llm_lab.cfunction import (
+    FunctionRange,
+    braces_balanced,
+    function_name,
+    function_range,
+    signature_text,
+    squash_code,
+)
 from llm_lab.conditions import Metric, read_metrics
-from llm_lab.fixer import WHOLE_FILE_MAX_LINES, FixError, split_lines, user_error
+from llm_lab.fixer import (
+    FIX_SCHEMA,
+    NUMBER_PREFIX,
+    WHOLE_FILE_MAX_LINES,
+    Edit,
+    EditMismatch,
+    FixError,
+    FixResult,
+    Request,
+    _minimize,
+    apply_edits,
+    display_diff,
+    read_decisions,
+    share,
+    split_lines,
+    user_error,
+)
 from llm_lab.kinds.base import Cell, Kind
 from llm_lab.polyspace import (
     Finding,
@@ -35,6 +58,23 @@ STOP_WORDS = frozenset({"number", "of", "max", "maximum", "the"})
 
 # 함수를 통째로 다시 써야 하므로 이보다 긴 함수는 LLM에 보내지 않는다 (답이 잘린다)
 FUNCTION_MAX_LINES = 1000
+TIMEOUT = 300.0  # 함수를 통째로 쓰는 답이라 RTE(120초)보다 오래 기다린다
+# 두 번 물어도 쓸 수 없는 답이었을 때 화면 첫 줄에 보일 설명
+CALLING_FIX = "LLM이 부르는 곳 수 지표를 고치려 했습니다."
+SIGNATURE_CHANGED = "LLM이 함수 이름이나 인자를 바꿨습니다."
+COMMENT_DROPPED = "LLM이 함수 위 주석을 지웠습니다."
+BRACES = "LLM 코드의 중괄호 짝이 맞지 않습니다."
+
+# 답 형식: 판단과 이유는 행마다(공통 형식과 같음), 수정은 바꿀 범위를 통째로 대신할 code
+CODE_SCHEMA = {
+    "type": "object",
+    "properties": {
+        "findings": FIX_SCHEMA["properties"]["findings"],
+        "code": {"type": "string"},
+    },
+    "required": ["findings", "code"],
+    "additionalProperties": False,
+}
 
 SYSTEM_PROMPT = """\
 You refactor one C function so that its Polyspace code metrics fall within the project's \
@@ -336,6 +376,69 @@ def prompt_tail(context: Any, findings: list[Finding], source: SourceFile) -> st
     return "\n\n".join(parts)
 
 
+def to_results(data: dict[str, Any], request: Request) -> list[FixResult]:
+    """행별 판단과, fix가 하나라도 있으면 code로 만든 묶음 공통 수정.
+
+    모두 no_fix면 code는 버린다.
+    """
+    decisions = read_decisions(data, len(request.findings))
+    pairs = zip(decisions, request.findings, strict=True)
+    for index, ((decision, _), finding) in enumerate(pairs, start=1):
+        if decision == "fix" and metric_words(finding.check) == CALLING:
+            raise EditMismatch(
+                f"Finding {index}은 Number of Calling Functions라 no_fix여야 함",
+                "format",
+                CALLING_FIX,
+            )
+    if all(decision == "no_fix" for decision, _ in decisions):
+        return share(decisions, (), "")
+    edits, diff = _code_edits(data.get("code"), request)
+    return share(decisions, edits, diff)
+
+
+def _code_edits(code: Any, request: Request) -> tuple[tuple[Edit, ...], str]:
+    """바꿀 범위를 code로 바꾼 뒤, 실제로 바뀐 줄만 남긴 수정과 화면용 diff."""
+    source = request.source
+    if not isinstance(code, str) or not code.strip():
+        raise EditMismatch("fix인데 code가 비었음", "format")
+    code = code.replace("\r\n", "\n")
+    filled = [line for line in code.split("\n") if line.strip()]
+    if all(NUMBER_PREFIX.match(line) for line in filled):
+        # 보인 코드의 줄 번호 표시('   57| ')까지 베꼈으면 뗀다
+        code = "\n".join(NUMBER_PREFIX.sub("", line, count=1) for line in code.split("\n"))
+    try:
+        code.encode(source.encoding)
+    except UnicodeEncodeError as exc:
+        char = ord(code[exc.start])
+        raise EditMismatch(
+            f"code에 {source.encoding}로 쓸 수 없는 문자 U+{char:04X}가 있음 - "
+            "ASCII나 한글만 쓰세요",
+            "encoding",
+        ) from None
+    if not braces_balanced(code):
+        raise EditMismatch("code의 중괄호 짝이 맞지 않음", "format", BRACES)
+    text = source.text
+    found = _function(text, request.findings)
+    if signature_text(text, found) not in squash_code(code):
+        raise EditMismatch(
+            "함수 시그니처(이름, 인자, 반환형)가 바뀜 - 그대로 두세요", "format", SIGNATURE_CHANGED
+        )
+    lines = split_lines(text)
+    comment = " ".join("\n".join(lines[found.start - 1 : found.signature - 1]).split())
+    if comment not in " ".join(code.split()):
+        raise EditMismatch(
+            "함수 바로 위 주석이 빠지거나 바뀜 - 그대로 두세요", "format", COMMENT_DROPPED
+        )
+    begin = sum(len(line) + 1 for line in lines[: found.start - 1])
+    end = min(len(text), sum(len(line) + 1 for line in lines[: found.end]))
+    # 범위 끝 줄바꿈과 code 끝 줄바꿈을 맞춘다 (안 맞추면 다음 줄이 붙거나 빈 줄이 생긴다)
+    code = code.rstrip("\n") + ("\n" if text[end - 1 : end] == "\n" else "")
+    edits = tuple(sorted(_minimize(text, Edit(begin, end, code)), key=lambda edit: edit.start))
+    if not edits:
+        raise EditMismatch("수정해도 원본과 같음")
+    return edits, display_diff(source.name, text, apply_edits(text, edits))
+
+
 def stage_of(finding: Finding) -> str:
     return finding.category or UNKNOWN
 
@@ -368,5 +471,8 @@ CODEMETRICS = Kind(
     facts=facts,
     prompt_tail=prompt_tail,
     region=region,
+    answer_schema=CODE_SCHEMA,
+    to_results=to_results,
+    timeout=TIMEOUT,
     auto_select=not_middle,
 )
````

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_codemetrics_answer.py -q`
Expected: `11 passed`

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0 (계획 확인 때 `406 passed`), 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/kinds/codemetrics.py tests/test_codemetrics_answer.py
git commit -m "feat: turn the LLM's rewritten function into minimal shared edits" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 웹 흐름 확인 (테스트만)

앞 태스크들을 웹 흐름 전체로 확인하는 테스트만 더한다. 공통 코드는 종류를 일반적으로 다루므로 고칠 코드가 없다. 그래서 테스트가 바로 통과해야 정상이다. 실패하면 앞 태스크나 기반의 문제이므로, 그 원인을 고친다.

**Files:**
- Test: `tests/test_codemetrics_web.py`(새 파일)

**Interfaces:**
- Consumes: `web.create_app`, `Worker`, `Store`, `patch.APPLY_COMMAND`, Task 2의 시험 데이터
- Produces: 없음

- [ ] **Step 1: 테스트 쓰기**

````diff
diff --git a/tests/test_codemetrics_web.py b/tests/test_codemetrics_web.py
new file mode 100644
index 0000000..a8e32fd
--- /dev/null
+++ b/tests/test_codemetrics_web.py
@@ -0,0 +1,150 @@
+"""CodeMetrics 작업: 엑셀 올리기 → 함수마다 LLM 한 번 → 결과 화면 → 패치 적용."""
+
+import shutil
+import subprocess
+
+import pytest
+from codemetrics_fixtures import (
+    GRADE_C,
+    GRADE_REFACTORED,
+    code_answer,
+    metric_row,
+    metrics_excel,
+)
+from fake_llm import MODEL, make_client, scripted
+from fastapi.testclient import TestClient
+
+from llm_lab import web
+from llm_lab.patch import APPLY_COMMAND
+from llm_lab.store import Store
+from llm_lab.worker import Worker
+
+SCALE_PATH = r"C:\proj\src\scale.c"
+SCALE_C = (
+    b"int scale(int x)\n{\n    if (x > 10) {\n        x = 10;\n    }\n    return x;\n}\n".replace(
+        b"\n", b"\r\n"
+    )
+)
+SCALE_REFACTORED = """\
+/* 상한을 적용한다. */
+static int scale_limit(int x)
+{
+    int limited = x;
+    if (limited > 10) {
+        limited = 10;
+    }
+    return limited;
+}
+
+int scale(int x)
+{
+    x = scale_limit(x);
+    return x;
+}
+"""
+CHOICES = ["codemetrics:Fail", "codemetrics:가운데", "codemetrics:단계 모름"]
+EXCEL = metrics_excel(
+    metric_row(actual=24),  # 엑셀 2행: grade, Fail
+    metric_row("Number of Calling Functions", threshold=4, actual=6),  # 3행: grade, 가운데
+    metric_row(actual=15, function="scale()", line=1, file=SCALE_PATH),  # 4행: scale, 가운데
+)
+ANSWERS = (
+    code_answer(
+        ("fix", "등급 계산을 도우미로 옮김"), ("no_fix", "공용 함수"), code=GRADE_REFACTORED
+    ),
+    code_answer(("fix", "상한 처리를 도우미로 옮김"), code=SCALE_REFACTORED),
+)
+
+
+def run_now(task):
+    task()
+
+
+def make_service(tmp_path, *responses):
+    handler, sent = scripted(*responses)
+    store = Store(tmp_path / "fixer.db")
+    worker = Worker(store, make_client(handler), MODEL, run_now)
+    client = TestClient(web.create_app(store, worker), base_url="http://127.0.0.1:8000")
+    return client, store, sent
+
+
+def source_folder(tmp_path):
+    root = tmp_path / "proj"
+    (root / "src").mkdir(parents=True)
+    (root / "src" / "grade.c").write_bytes(GRADE_C)  # CP949, LF
+    (root / "src" / "scale.c").write_bytes(SCALE_C)  # UTF-8, CRLF
+    return root
+
+
+def upload(client, excel, folder, choices=CHOICES):
+    files = [("excel", ("metrics.xlsx", excel))]
+    data = {"source_dir": str(folder), "kind": "codemetrics", "choice": list(choices)}
+    return client.post("/results", files=files, data=data, follow_redirects=False)
+
+
+def test_home_offers_codemetrics_with_every_stage_checked(tmp_path):
+    client, _, _ = make_service(tmp_path)
+
+    page = client.get("/").text
+
+    assert 'name="kind" value="codemetrics">' in page
+    for stage in ("Fail", "가운데", "단계 모름"):
+        assert f'name="choice" value="codemetrics:{stage}" checked' in page
+
+
+def test_one_request_per_function_and_middle_fixes_are_left_to_people(tmp_path):
+    client, store, sent = make_service(tmp_path, *ANSWERS)
+
+    assert upload(client, EXCEL, source_folder(tmp_path)).status_code == 303
+
+    job = store.job(1)
+    assert (job.kind, job.sheet, job.state) == ("codemetrics", "CodeMetrics_Result", "done")
+    assert len(sent) == 2  # 함수마다 한 번, Fail이 든 grade부터
+    first = sent[0]["messages"][1]["content"]
+    assert "Replace lines 3-15 of src/grade.c" in first
+    assert "Finding 2:\n- check: Number of Calling Functions" in first
+    assert sent[0]["response_format"]["json_schema"]["schema"]["required"] == ["findings", "code"]
+    decisions = [(row.finding.row, row.result.decision) for row in store.rows(1) if row.result]
+    assert decisions == [(2, "fix"), (3, "no_fix"), (4, "fix")]
+
+    page = client.get("/results/1").text
+    assert "값 24 · 기준 10 · 조건 시트: Fail (고칠 것)" in page
+    assert "조건 시트: 지표 3개" in page
+    assert 'name="row" value="1" data-auto="1"' in page  # Fail
+    assert 'name="row" value="3" data-auto="0"' in page  # 가운데
+    assert "자동으로 고르지 않은 수정 1개는 직접 고르세요" in page
+
+
+def test_unchosen_stages_are_not_sent(tmp_path):
+    only_fail = code_answer(("fix", "등급 계산을 도우미로 옮김"), code=GRADE_REFACTORED)
+    client, store, sent = make_service(tmp_path, only_fail)
+
+    upload(client, EXCEL, source_folder(tmp_path), choices=["codemetrics:Fail"])
+
+    assert len(sent) == 1
+    assert [row.finding.row for row in store.rows(1)] == [2]
+    assert "가운데 (고르지 않음) 2" in store.job(1).note
+
+
+@pytest.mark.skipif(shutil.which("git") is None, reason="git 없음")
+def test_patch_applies_byte_for_byte_to_cp949_and_crlf_files(tmp_path):
+    client, _, _ = make_service(tmp_path, *ANSWERS)
+    folder = source_folder(tmp_path)
+    upload(client, EXCEL, folder)
+
+    response = client.post("/results/1/fixes.patch", data={"row": ["1", "3"]})
+    assert response.status_code == 200
+    (folder / "fixes.patch").write_bytes(response.content)
+    for args in (["--check"], []):
+        subprocess.run(
+            [*APPLY_COMMAND.split(), *args, "fixes.patch"],
+            cwd=folder,
+            check=True,
+            capture_output=True,
+        )
+
+    grade = ('#include "grade.h"\n\n' + GRADE_REFACTORED).encode("cp949")
+    assert (folder / "src" / "grade.c").read_bytes() == grade
+    assert (folder / "src" / "scale.c").read_bytes() == SCALE_REFACTORED.replace(
+        "\n", "\r\n"
+    ).encode()
````

- [ ] **Step 2: 통과 확인**

Run: `uv run pytest tests/test_codemetrics_web.py -q`
Expected: `4 passed`. git이 없으면 패치 적용 테스트 하나는 건너뛴다.

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0 (계획 확인 때 `410 passed`), 린트 통과

- [ ] **Step 3: 커밋**

```bash
git add tests/test_codemetrics_web.py
git commit -m "test: run a CodeMetrics job end to end and apply its patch" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 문서와 회사 PC 확인 (`README.md`, 설계 문서, `todo.md`)

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md`, `todo.md`

- [ ] **Step 1: README와 설계 문서**

````diff
diff --git a/README.md b/README.md
index dce0cdb..4867db1 100644
--- a/README.md
+++ b/README.md
@@ -1,6 +1,6 @@
 # llm-lab
 
-회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace 수정 제안 서비스(RTE·MISRA)입니다.
+회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace 수정 제안 서비스(RTE·MISRA·CodeMetrics)입니다.
 
 ## 준비
 
@@ -58,7 +58,7 @@ uv run llm-probe --context --output probe-result-context.txt
 
 ## 수정 제안 서비스 (llm-fix-server)
 
-Polyspace 결과 엑셀의 RTE 시트(Red/Orange) 또는 MISRA C:2012 시트에서 행마다 회사 LLM이 "수정" 또는 "수정 불필요"를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.
+Polyspace 결과 엑셀의 RTE 시트(Red/Orange), MISRA C:2012 시트, CodeMetrics 시트에서 행마다 회사 LLM이 "수정" 또는 "수정 불필요"를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.
 
 ```bash
 uv run llm-fix-server
@@ -69,6 +69,8 @@ uv run llm-fix-server
    - "분석할 시트"에서 RTE나 MISRA를 고르고 분석할 대상을 체크합니다. RTE는 Red·Orange, MISRA는 Mandatory·Required·Advisory이고, MISRA는 처음에 Mandatory와 Required만 체크되어 있습니다.
    - 엑셀은 이름이 `_Result`로 끝나고 종류 이름(`rte`, `misra`)이 들어간 시트를 읽습니다. 그 시트에 `TYPE`, `File`, `line`, `check`, `detail` 열이 있어야 합니다.
    - MISRA는 엑셀의 조건 시트(이름은 상관없고 `Guideline`, `Mode` 열이 있는 시트)에서 규칙마다 분류(`Mode`)와 적용 여부(`Enabled`)를 작업마다 읽습니다. 꺼진 규칙은 빼고, 분류는 조건 시트를 따릅니다. 조건 시트가 없으면 결과 시트의 `Category`를 씁니다.
+   - CodeMetrics는 이름에 `metric`이 들어간 `_Result` 시트에서 `File`, `Function`, `line`, `Check`, `Threshold`, `Actual Value` 열을 읽습니다. 대상은 Fail·가운데·단계 모름이고 처음에 모두 체크되어 있습니다.
+   - CodeMetrics의 단계는 엑셀의 조건 시트(이름은 상관없고 범위 열과 판정 열이 있는 시트)에서 작업마다 읽고, `Actual Value`가 든 범위로 정합니다. 조건 시트상 Pass인 행과 `Function`이 빈 행은 뺍니다. 결과 시트와 조건 시트의 지표 이름이 다르면 낱말로, 그래도 안 되면 한도 값으로 짝짓고, 결과는 작업 메모에 개수로 보입니다.
    - "폴더 찾기…" 버튼을 누르면 윈도우 폴더 선택 창이 뜨고, 고른 폴더 경로가 칸에 채워집니다. 창이 안 보이면 작업 표시줄을 확인하세요.
    - 경로를 직접 붙여넣어도 됩니다(탐색기 주소창에서 복사, 따옴표가 붙어 있어도 됨).
    - 엑셀에 나온 파일만 그 폴더 안에서 찾아 읽습니다. 같은 이름 파일이 여러 개면 엑셀 경로와 폴더 구조가 가장 길게 맞는 것을 고르고, `.git`처럼 점으로 시작하는 폴더는 보지 않습니다.
@@ -76,12 +78,14 @@ uv run llm-fix-server
    - 최근에 쓴 폴더는 입력 칸에서 목록으로 고를 수 있습니다.
 3. 처리 중에는 결과 페이지가 진행 막대와 함께 5초마다 새로 고쳐집니다. 결과는 탭 두 개로 봅니다.
    - MISRA는 같은 파일의 같은 줄에 걸린 지적을 묶어 LLM에 한 번에 보냅니다. 판단과 이유는 행마다 받고, 수정은 묶음이 함께 씁니다.
+   - CodeMetrics는 같은 함수에 걸린 지표를 묶어 함수마다 LLM에 한 번 보냅니다. LLM은 그 함수(바로 위 주석 포함)를 통째로 다시 쓰고, 서버는 바뀐 줄만 남겨 diff와 패치를 만듭니다. 함수 이름·인자·반환형이 바뀌었거나 위 주석이 빠졌으면 받지 않고 한 번 다시 묻습니다. Number of Calling Functions(부르는 곳 수)는 다른 파일을 바꿔야 하므로 늘 "수정 불필요"입니다.
    - 중요한 대상(Red, Mandatory)부터 처리합니다. "멈춤"을 누르면 그때까지 처리된 행으로 패치를 받을 수 있고, "이어서 처리"로 남은 행을 계속합니다.
    - "한눈에 보기": 맨 위 개수(Red/Orange, 수정 / 수정 불필요 / 확인 필요), 검사 종류별·파일별 표, 행마다 한 줄짜리 표. "보기"를 누르면 그 행의 diff로 갑니다.
    - "자세히 보기 (diff)": 행마다 판단 근거와 diff
    - "확인 필요"는 LLM이 처리하지 못했거나 소스를 찾지 못한 행입니다. 무엇이 문제인지와 할 일을 두 줄로 보여주고, 개발자용 내용과 대화 기록 파일 이름은 "자세히 (개발자용)"을 펼치면 보입니다.
 4. 작업이 끝나면 적용할 수정에 체크하거나 "수정 모두 선택"을 누른 뒤 "패치 내려받기"를 누릅니다.
    - 서로 다른 수정이 같은 줄을 고치면 함께 적용할 수 없습니다. "수정 모두 선택"은 중요한 대상(Red, Mandatory)을 먼저, 그다음 엑셀 행 순서로 겹치지 않는 것만 고릅니다.
+   - CodeMetrics의 가운데 단계 수정은 "수정 모두 선택"이 고르지 않습니다. diff를 검토한 뒤 직접 체크하세요. 같은 함수의 수정은 하나라서, 그 함수의 행 하나만 골라도 리팩터링 전체가 패치에 들어갑니다.
    - 빠진 행에는 "이번엔 제외 · 행 N 수정과 같은 줄을 고칩니다"가 붙습니다. 직접 체크하면 겹치는 상대 행의 체크가 풀립니다.
    - 빠진 행은 패치를 적용하고 Polyspace를 다시 돌린 뒤, 새 엑셀로 다시 분석하면 됩니다. 이미 고쳐졌으면 다음 결과에 나오지 않습니다.
 5. 화면에 나온 폴더에서 패치를 확인한 뒤 적용합니다. `core.autocrlf=false`는 Git for Windows가 LF 파일을 CRLF로 바꾸지 않게 합니다.
diff --git a/docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md b/docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md
index 5bc98d7..3ce5db3 100644
--- a/docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md
+++ b/docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md
@@ -1,7 +1,7 @@
 # Polyspace CodeMetrics 리팩터링 제안 (llm-fix-server의 CodeMetrics 종류) 설계
 
 - 작성일: 2026-10-08
-- 상태: 승인됨 (2026-10-08). 구현 계획은 공용 기반이 master에 올라온 뒤 쓴다.
+- 상태: 구현됨. 구현 계획: `docs/superpowers/plans/2026-10-12-codemetrics-fixer.md`
 - 선행 조건: 시트 종류 공용 기반(MISRA 준비 작업에서 만든다)이 master에 올라온 뒤 구현한다. 13절이 그 기반에 바라는 것이다.
 - 이 저장소는 공개다. 이 문서와 테스트에는 회사 엑셀의 실제 값(시트 이름, 행 수, 기준치, 파일·함수 이름)을 넣지 않고 일반적인 예만 쓴다.
 - 조건(기준치, 단계 수, 지표 목록, 열 배치)은 회사 양식이라 바뀔 수 있다. 코드·프롬프트·테스트에 상수로 박지 않고 작업마다 엑셀에서 읽는다(사용자, 2026-10-08).
@@ -69,7 +69,7 @@ Polyspace 옵션 `-consider-switch-as-single-decision`을 쓰면 `switch` 전체
 
 - 결과 시트에는 Polyspace가 한도를 넘었다고 본 행만 올라온다고 본다. 그래서 "클수록 나쁨" 같은 방향을 가정해 `Threshold`와 비교하지 않는다. 낮을수록 나쁜 지표(예: Comment Density)도 조건 시트의 범위만 맞으면 그대로 동작한다.
 - `Threshold`와 `Actual Value`는 화면과 프롬프트에 보여 주기만 한다.
-- 제외한 행 수는 이유별로 작업에 남긴다. "단계 모름" 행 수도 이유별로 따로 센다(조건 시트 없음 / 지표 짝 없음 / 값이 어느 범위에도 들지 않음 / 범위 글을 읽지 못함 / 값이 숫자가 아님).
+- 제외한 행 수는 이유별로 작업에 남긴다. "단계 모름"의 이유(조건 시트 없음 / 조건 시트에 짝이 없는 지표 / 어느 범위에도 들지 않음 / 범위 글을 읽지 못함 / Actual Value가 숫자가 아님)는 그 행의 메모에 적고, 지표 짝 개수는 작업 메모에 적는다. 단계 모름 행 수는 개수 칸의 배지로 보인다.
 - 숫자는 정수·소수, 그리고 쉼표 자릿수 표기(`1,234`)를 받는다.
 - `line`이 정수가 아니면 그 묶음은 RTE처럼 "줄 번호 오류"다.
 
````

다른 작업이 README를 고쳐 diff가 맞지 않으면, 같은 문장을 같은 자리에 손으로 넣는다. 자리는 다음과 같다.
- MISRA 조건 시트 문단 다음
- MISRA 묶음 문단 다음
- "수정 모두 선택" 문단 다음

- [ ] **Step 2: todo.md에 회사 PC 확인 절을 더한다**

`todo.md`는 다른 작업도 자주 고치므로 diff 대신 넣을 글을 적는다. 절 번호 N은 그때 마지막 절 번호에 1을 더한 값이다. "순서 요약" 표 끝에 다음 줄을 더한다.

```markdown
| N | CodeMetrics 분석해 보기 | 함수 수에 따라 다름 |
```

파일 끝에 다음 절을 붙인다.

````markdown
## N. CodeMetrics 분석해 보기

CodeMetrics 시트도 RTE처럼 분석할 수 있게 됐습니다. 함수마다 LLM을 한 번 불러, 함수를 나누는(리팩터링) 수정안을
받습니다. 1번(`git pull`)을 했다면 새로 설치할 것은 없습니다.

1. `uv run llm-fix-server`로 켜고, ① 같은 엑셀, ② 같은 소스 폴더를 고릅니다.
2. "분석할 시트"에서 CodeMetrics를 고르고 "분석 시작"을 누릅니다. 단계(Fail, 가운데, 단계 모름)는 모두 체크되어 있습니다.
   - 함수를 통째로 다시 쓰므로 RTE보다 오래 걸립니다. 함수 하나에 수십 초쯤 걸릴 수 있습니다.
3. 결과 화면 맨 위의 작업 메모와 숫자를 봅니다.
4. "자세히 보기 (diff)"에서 수정을 검토합니다. 문장을 도우미 함수로 옮기기만 했는지, 계산 순서나 동작이 바뀐 곳은 없는지 봅니다.
5. "수정 모두 선택"을 누릅니다. Fail과 단계 모름만 골라집니다.
   - 검토한 가운데 단계 수정은 직접 체크합니다.
   - "패치 내려받기"를 누르고, `--check` 먼저 적용한 뒤 Polyspace를 다시 돌립니다.
6. 새 엑셀로 지표별 행 수를 봅니다. 이 화면은 이름 없이 숫자만 나옵니다.

```bash
uv run python scripts/peek_metrics.py "새 엑셀 경로.xlsx"
```

알려줄 것(개수와 종류만, 코드·이름·경로 없이):

- 작업 메모의 숫자. 화면에는 엑셀 파일 이름도 보이므로 캡처하지 말고 숫자만 적습니다.
  - `조건 시트: 지표 N개`, `조건 시트에서 짝을 찾지 못한 지표 N개`, `값으로 이은 지표 N개`
  - 뺀 행의 개수
- 맨 위 숫자: Fail, 가운데, 단계 모름, 그리고 수정, 수정 불필요, 확인 필요
- "확인 필요" 행 설명 첫 줄의 종류와 개수. 첫 줄은 정해 둔 문구라 그대로 적어도 됩니다.
- diff 검토에서 동작이 바뀐 것 같은 수정이 몇 개였고 어떤 식이었는지. 코드와 이름 없이 말로 적습니다.
- `git apply`가 됐는지. 안 됐으면 오류 종류만 적습니다.
- 6번 화면 캡처. 분석 전 화면과 비교합니다.
- Polyspace를 다시 돌린 뒤 RTE·MISRA 위반이 새로 생겼는지, 개수만
- 함수 하나 처리에 대략 몇 초 걸렸는지
````

- [ ] **Step 3: 확인과 커밋**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: 실패 0, 린트 통과

```bash
git add README.md docs/superpowers/specs/2026-10-08-codemetrics-fixer-design.md todo.md
git commit -m "docs: document CodeMetrics and add its company PC check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 마무리

- [ ] 전체 테스트와 린트를 한 번 더 돌리고, 저장소 규칙대로 origin master에 올린다.
  - 올리기 전에, 바뀐 파일에 회사 값(엑셀·소스·실제 기준치·이름)이 없는지 확인한다.

```bash
git diff --stat origin/master
```
