# 시트 종류 기반과 MISRA 지원 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polyspace 결과 엑셀의 시트 종류(RTE, MISRA)를 한곳에서 정의하는 기반을 만들고, 그 위에서 MISRA C:2012 시트를 RTE처럼 분석하게 한다. MISRA는 같은 줄의 지적을 묶어 LLM에 한 번에 보내고, 분류와 적용 여부는 엑셀의 조건 시트에서 읽는다.

**Architecture:** 종류마다 다른 것(읽을 시트·열, 분석할 행, 묶음, 지시문, 답 형식, 화면 표시)은 `kinds/`의 `Kind` 하나에 모은다.
- 엑셀 읽기(`polyspace.read_sheet`), LLM 요청(`fixer.make_request`·`propose_fixes`), 작업자(묶음 단위), 화면은 종류를 인자로 받는 공통 코드다.
- LLM 호출 하나가 묶음 하나를 맡는다. 판단과 이유는 행마다 받고, 수정은 묶음이 함께 쓴다. 패치·충돌 코드는 똑같은 수정을 이미 하나로 합치므로 바꾸지 않는다.
- DB는 스키마 3으로 올린다(작업의 종류·메모·작업 정보, 행의 묶음).

**Tech Stack:** Python 3.14, uv, openai 3.x(httpx2), FastAPI, openpyxl, sqlite3, difflib, pytest, ruff. 새 의존성은 없다.

**Spec:** `docs/superpowers/specs/2026-10-08-sheet-kinds-misra-design.md`

## Global Constraints

- Python `>=3.14`, 명령은 모두 `uv run …`으로 실행한다.
- ruff: `line-length = 100`(한글은 폭 2로 센다), 규칙 `E, F, I, UP, B`. 태스크 끝마다 `uv run ruff check`와 `uv run ruff format --check`가 통과해야 한다.
- 코드 주석·docstring·화면 문구는 한국어로 쓴다. LLM 시스템 메시지는 영어로 쓰고 `reason`만 한국어로 받는다.
- 테스트는 네트워크를 쓰지 않는다. LLM은 `tests/fake_llm.py`로 흉내 낸다.
- 새 런타임·개발 의존성은 없다.
- 공개 저장소다. 회사 엑셀의 값(시트 이름, 행 수, 파일·함수 이름, 칸 원문, 규칙별 분류와 적용 여부)을 코드, 테스트, 문서, 커밋에 넣지 않는다. 테스트 데이터는 만들어 낸 값만 쓴다(설계 10절).
- 조건 값(규칙 번호, 분류, 적용 여부, 시트 이름)은 코드와 지시문에 고정하지 않고 작업마다 엑셀에서 읽는다(설계 3.5절).
- `src/llm_lab/conditions.py`(다른 작업의 모듈)는 건드리지 않는다.
- `patch.py`와 `patch.APPLY_COMMAND`는 바꾸지 않는다.
- 예전 DB(스키마 2)와 그 안의 RTE 작업이 그대로 열려야 한다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`을 붙인다.

## Review Focus

설계가 직접 말하지 않지만 실제로 부딪히기 쉬운 경우 다섯 가지다. 각각을 맡은 태스크에 테스트로 고정했다.

1. **조건 시트의 규칙 번호가 숫자 칸에 저장됨:** 엑셀이 `8.10`을 숫자로 저장하면 `8.1`로 읽혀 진짜 8.1과 겹친다. 겹친 번호는 어느 쪽인지 모르므로 쓰지 않고, 작업 메모에 남겨야 한다. (Task 4 `test_rule_numbers_that_collide_are_not_used`)
2. **결과 시트의 디렉티브 행:** Check가 번호로만 시작하고 Group에 Directive가 있는 행은 같은 번호의 Rule 조건을 가져오면 안 된다. (Task 4 `test_directive_rows_use_directive_conditions`)
3. **엑셀을 다시 뽑아 행 번호만 바뀜:** 같은 묶음이면 LLM을 다시 부르지 않고, 결과가 제 행에 붙어야 한다. (Task 3 `test_group_result_is_reused_when_only_excel_rows_change`)
4. **한 줄에 같은 규칙이 두 번:** check와 detail이 같은 두 행도 각자 답을 받아야 한다. (Task 3 `test_same_rule_twice_on_one_line_gets_an_answer_each`)
5. **멈춘 작업에서 패치 받기:** 멈추기 전에 끝난 행으로 패치를 받을 수 있어야 한다. (Task 5 `test_stopped_job_still_gives_a_patch_for_finished_rows`)

---

## 이 계획의 코드

- 각 단계의 코드는 저장소 루트에서 `git apply`로 적용하는 diff다. 블록 내용을 파일로 저장해 `git apply 파일`을 실행하거나, 같은 내용을 손으로 고친다.
- 이 diff들은 계획을 쓸 때 임시 사본에서 태스크마다 실제로 돌려 확인했다. 순서는 "테스트 diff 적용 → 실패 확인 → 구현 diff 적용 → 통과·린트 확인"이고, 결과 트리는 태스크 커밋과 같았다.
- 작업 폴더가 CRLF(Git for Windows 기본 `core.autocrlf=true`)여도 `git apply`가 맞춰 적용한다. 실제 작업 폴더에서 `git apply --check`로 확인했다.
- 통과한 테스트 수는 계획을 쓸 때의 master(`0ac4a37`) 기준이다. 그 뒤 다른 작업이 테스트를 더했으면 그만큼 많다. 실패가 0이면 된다.
- 시작하기 전에 작업 브랜치를 origin/master 위로 맞춘다(`git fetch origin` 뒤 `git rebase origin/master`).

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `src/llm_lab/kinds/__init__.py` | `KINDS` 목록 (이름 → `Kind`) | 1, 4 |
| `src/llm_lab/kinds/base.py` | `Kind` 정의와 공통 기본 함수 | 1, 2, 4, 5 |
| `src/llm_lab/kinds/rte.py` | RTE 종류: 행 읽기, 지시문 | 1, 2 |
| `src/llm_lab/kinds/misra.py` | MISRA 종류: 조건 시트, 행 읽기, 묶음, 지시문 | 4 |
| `src/llm_lab/polyspace.py` | `read_sheet`, `Finding`, `Prepared`, `Sheet`, 헤더·칸 읽기 도우미 | 1 |
| `src/llm_lab/fixer.py` | `make_request`, `propose_fixes`, `cache_key`, 공통 답 처리 | 2 |
| `src/llm_lab/store.py` | 스키마 3, `claim_rows`, `stop`, 캐시 목록 | 2, 3 |
| `src/llm_lab/worker.py` | 묶음 단위 처리, 중요한 대상부터 | 2, 3 |
| `src/llm_lab/transcript.py` | 묶음마다 대화 기록 하나 | 3 |
| `src/llm_lab/web.py` | 종류·대상 폼 필드, 멈춤 경로, 작업 메모·작업 정보 저장 | 1, 5 |
| `src/llm_lab/pages.py` | 종류 고르기, 배지, 메모, 묶음 표시, 멈춤과 이어서 처리 | 5 |
| `tests/rte_fixtures.py` | 공통 답 형식 도우미 `answer()` | 2 |
| `tests/misra_fixtures.py` | MISRA 시험 데이터(만들어 낸 값) | 4 |
| `tests/test_kinds.py` | 종류 정의 테스트 | 1, 4 |
| `README.md`, RTE 설계 문서, `todo.md` | 문서와 회사 PC 확인 절 | 6 |

---

### Task 1: 결과 시트를 종류로 읽기 (`polyspace.py`, `kinds/`)

**Files:**
- Modify: `src/llm_lab/polyspace.py` (`read_rte` → `read_sheet`, 도우미 공개, `Finding` 새 필드, `Prepared`, `Sheet`)
- Create: `src/llm_lab/kinds/__init__.py`, `src/llm_lab/kinds/base.py`, `src/llm_lab/kinds/rte.py`
- Modify: `src/llm_lab/web.py` (`read_sheet(…, RTE, RTE.defaults)`로 부르고, 뺀 행 수는 합계로 저장)
- Test: `tests/test_polyspace.py`(바꿈), `tests/test_kinds.py`(새 파일)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `polyspace.Finding`: 기존 필드에 `category`, `note`, `threshold`, `actual`, `stage`(모두 기본값 `""`)를 더한다.
  - `polyspace.Prepared(data: Any = None, note: str = "")`
  - `polyspace.Sheet(sheet: str, findings: list[Finding], skipped: dict[str, int], prepared: Prepared = Prepared())`
  - 도우미: `norm(value) -> str`, `column_name(value) -> str`(끝 콜론도 무시), `cell_text(value) -> str`, `line_number(value) -> int | None`, `find_header(sheet, required) -> tuple[int, dict[str, int]] | str`, `data_rows(sheet, header_row, columns) -> Iterator[tuple[int, Callable[[str], Any]]]`
  - `read_sheet(source: IO[bytes], kind: Kind, selected: Collection[str]) -> Sheet`, `SheetError`
  - `kinds.Kind`(frozen, `eq=False`): `name`, `title`, `sheet_word`, `required`, `read_row(cell, row, context) -> Finding | str`, `label(finding) -> str`, `choices`, `defaults`, `colors`, `prepare(workbook) -> Prepared`(기본 `nothing_to_prepare`). 메서드: `rank(label) -> int`, `priority(finding) -> int`, `color(label) -> str`
  - `kinds.Cell = Callable[[str], Any]`, `kinds.RTE`, `kinds.KINDS: dict[str, Kind]`

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_kinds.py b/tests/test_kinds.py
new file mode 100644
index 0000000..67400da
--- /dev/null
+++ b/tests/test_kinds.py
@@ -0,0 +1,31 @@
+from rte_fixtures import RTE_HEADER, finding, rte_row
+
+from llm_lab.kinds import KINDS, RTE
+from llm_lab.polyspace import Finding, column_name
+
+
+def cells(header: list, values: list):
+    """read_row에 넘기는 칸 읽기 함수: 열 이름 → 그 행의 값 (없는 열이면 None)."""
+    row = dict(zip([column_name(name) for name in header], values, strict=True))
+    return row.get
+
+
+def test_rte_reads_red_and_orange_rows_and_names_other_types():
+    red = RTE.read_row(cells(RTE_HEADER, rte_row("Red  check", line=7)), 5, None)
+
+    assert isinstance(red, Finding)
+    assert (red.row, red.color, red.type, red.line) == (5, "Red", "Red  check", 7)
+    assert RTE.read_row(cells(RTE_HEADER, rte_row("Gray Check")), 6, None) == "Gray Check"
+    assert RTE.read_row(cells(RTE_HEADER, rte_row(None)), 7, None) == "(빈 TYPE)"
+
+
+def test_rte_priority_and_colors():
+    assert RTE.priority(finding(color="Red")) < RTE.priority(finding(color="Orange"))
+    assert RTE.rank("기타") == len(RTE.choices)  # choices에 없는 글자는 맨 뒤
+    assert (RTE.color("Red"), RTE.color("Orange"), RTE.color("기타")) == ("red", "orange", "gray")
+
+
+def test_kinds_have_unique_names_and_defaults_among_choices():
+    assert all(name == kind.name for name, kind in KINDS.items())
+    for kind in KINDS.values():
+        assert set(kind.defaults) <= set(kind.choices)
diff --git a/tests/test_polyspace.py b/tests/test_polyspace.py
index 9786fce..044f421 100644
--- a/tests/test_polyspace.py
+++ b/tests/test_polyspace.py
@@ -1,26 +1,30 @@
 import io
+from dataclasses import replace
 
 import pytest
 from rte_fixtures import RTE_HEADER, rte_excel, rte_row, workbook_bytes
 
-from llm_lab.polyspace import SheetError, read_rte
+from llm_lab.kinds import RTE
+from llm_lab.polyspace import Finding, Prepared, SheetError, read_sheet
 
 
-def read(data: bytes):
-    return read_rte(io.BytesIO(data))
+def read(data: bytes, selected=("Red", "Orange")):
+    return read_sheet(io.BytesIO(data), RTE, selected)
 
 
-def test_reads_red_and_orange_and_skips_gray():
+def test_reads_red_and_orange_and_counts_skipped_rows_by_reason():
     data = rte_excel(
         rte_row("Red Check", line=7, check="Overflow"),
         rte_row("Gray Check"),
         rte_row("Orange Check", line=12),
+        rte_row("Gray Check"),
+        rte_row(None),
     )
 
     sheet = read(data)
 
     assert sheet.sheet == "RTE_Result"
-    assert sheet.skipped == 1
+    assert sheet.skipped == {"Gray Check": 2, "(빈 TYPE)": 1}
     assert [(f.row, f.color, f.line, f.check) for f in sheet.findings] == [
         (2, "Red", 7, "Overflow"),
         (4, "Orange", 12, "Division by zero"),
@@ -32,6 +36,15 @@ def test_reads_red_and_orange_and_skips_gray():
     assert first.id == "1"
 
 
+def test_unselected_targets_are_skipped_with_their_label():
+    data = rte_excel(rte_row("Red Check"), rte_row("Orange Check"), rte_row("Orange Check"))
+
+    sheet = read(data, selected=["Red"])
+
+    assert [f.color for f in sheet.findings] == ["Red"]
+    assert sheet.skipped == {"Orange (고르지 않음)": 2}
+
+
 def test_finds_header_below_title_rows_and_ignores_blank_rows():
     data = rte_excel(
         rte_row(line=5),
@@ -43,27 +56,34 @@ def test_finds_header_below_title_rows_and_ignores_blank_rows():
     sheet = read(data)
 
     assert [(f.row, f.line) for f in sheet.findings] == [(5, 5), (7, 6)]
-    assert sheet.skipped == 0
+    assert sheet.skipped == {}
 
 
-def test_header_match_ignores_case_spaces_and_line_breaks():
-    # 엑셀 셀 안 줄바꿈(Alt+Enter)과 앞뒤 공백이 있어도 같은 열로 본다
-    header = [" type ", "FILE\n", "Line", "CHECK", "Detail"]
+def test_header_match_ignores_case_spaces_line_breaks_and_trailing_colons():
+    # 엑셀 셀 안 줄바꿈(Alt+Enter), 앞뒤 공백, 끝의 콜론이 있어도 같은 열로 본다
+    header = [" type ", "FILE\n", "Line :", "CHECK:", "Detail"]
     data = workbook_bytes({"RTE_Result": [header, ["red  check", "a.c", " 57 ", "Overflow", "x"]]})
 
     sheet = read(data)
 
     assert sheet.findings[0].color == "Red"
     assert sheet.findings[0].line == 57
+    assert sheet.findings[0].check == "Overflow"
     assert sheet.findings[0].group == ""  # 없는 선택 열은 빈 문자열
 
 
-def test_prefers_rte_sheet_when_several_result_sheets_match():
+def test_sheet_is_chosen_by_name_because_kinds_share_columns():
     rows = [RTE_HEADER, rte_row()]
     data = workbook_bytes({"MISRA_Result": rows, "RTE_Result": rows, "Other": rows})
 
     assert read(data).sheet == "RTE_Result"
 
+    with pytest.raises(SheetError) as caught:
+        read(workbook_bytes({"MISRA_Result": rows}))
+    message = str(caught.value)
+    assert message.startswith("RTE 시트를 찾지 못했습니다.")
+    assert "MISRA_Result (이름에 rte가 없음)" in message
+
 
 def test_non_integer_line_becomes_none():
     data = rte_excel(rte_row(line="12"), rte_row(line=12.0), rte_row(line="n/a"))
@@ -75,7 +95,7 @@ def test_missing_required_column_lists_sheets_and_headers():
     data = workbook_bytes(
         {
             "Summary": [["x"]],
-            "MISRA_Result": [["ID", "Rule", "File", "Line"], [1, "10.1", "a.c", 3]],
+            "RTE_Result": [["ID", "Rule", "File", "Line"], [1, "10.1", "a.c", 3]],
         }
     )
 
@@ -84,9 +104,32 @@ def test_missing_required_column_lists_sheets_and_headers():
 
     message = str(caught.value)
     assert "Summary (이름이 _Result로 끝나지 않음)" in message
-    assert "MISRA_Result (찾은 헤더: ID, Rule, File, Line)" in message
+    assert "RTE_Result (찾은 헤더: ID, Rule, File, Line)" in message
 
 
 def test_not_an_excel_file():
     with pytest.raises(SheetError, match="엑셀"):
         read(b"not a zip file")
+
+
+def test_prepare_reads_the_whole_workbook_once_and_feeds_every_row():
+    seen = []
+
+    def prepare(workbook):
+        seen.append(sorted(sheet.title for sheet in workbook.worksheets))
+        return Prepared({"limit": 10}, "메모")
+
+    def read_row(cell, row, context):
+        found = RTE.read_row(cell, row, context)
+        if isinstance(found, Finding):
+            found = replace(found, note=f"limit {context['limit']}")
+        return found
+
+    kind = replace(RTE, prepare=prepare, read_row=read_row)
+    data = workbook_bytes({"Summary": [["x"]], "RTE_Result": [RTE_HEADER, rte_row(), rte_row()]})
+
+    sheet = read_sheet(io.BytesIO(data), kind, kind.defaults)
+
+    assert seen == [["RTE_Result", "Summary"]]
+    assert sheet.prepared == Prepared({"limit": 10}, "메모")
+    assert [f.note for f in sheet.findings] == ["limit 10", "limit 10"]
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q`
Expected: `ERROR tests/test_kinds.py`, `ERROR tests/test_polyspace.py` — `ModuleNotFoundError: No module named 'llm_lab.kinds'`

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/kinds/__init__.py b/src/llm_lab/kinds/__init__.py
new file mode 100644
index 0000000..b7f476c
--- /dev/null
+++ b/src/llm_lab/kinds/__init__.py
@@ -0,0 +1,8 @@
+"""Polyspace 결과 시트 종류. 새 종류는 이 패키지에 파일 하나를 더하고 KINDS에 넣는다."""
+
+from llm_lab.kinds.base import Cell, Kind
+from llm_lab.kinds.rte import RTE
+
+KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE,)}
+
+__all__ = ["KINDS", "RTE", "Cell", "Kind"]
diff --git a/src/llm_lab/kinds/base.py b/src/llm_lab/kinds/base.py
new file mode 100644
index 0000000..957e0d4
--- /dev/null
+++ b/src/llm_lab/kinds/base.py
@@ -0,0 +1,39 @@
+"""시트 종류(Kind): 종류마다 다른 것(읽을 시트·열, 분석할 행, 배지)을 한곳에 모은 정의."""
+
+from __future__ import annotations
+
+from collections.abc import Callable, Mapping
+from dataclasses import dataclass
+from typing import Any
+
+from llm_lab.polyspace import Finding, Prepared
+
+Cell = Callable[[str], Any]  # 열 이름 → 그 행의 칸 값 (없는 열이면 None)
+
+
+def nothing_to_prepare(workbook: Any) -> Prepared:
+    return Prepared()
+
+
+@dataclass(frozen=True, eq=False)
+class Kind:
+    name: str  # DB와 폼에 쓰는 이름
+    title: str  # 화면 이름
+    sheet_word: str  # 결과 시트 이름에 들어가는 낱말 (소문자)
+    required: tuple[str, ...]  # 필수 열 (polyspace.column_name으로 맞춘 이름)
+    read_row: Callable[[Cell, int, Any], Finding | str]  # 대상이면 Finding, 아니면 뺀 이유
+    label: Callable[[Finding], str]  # 배지 글자. 대상 고르기와 우선순위에도 쓴다
+    choices: tuple[str, ...]  # 첫 화면에서 고르는 대상. 중요한 것부터 적는다
+    defaults: tuple[str, ...]  # 처음에 체크된 대상
+    colors: Mapping[str, str]  # 배지 글자 → 배지 색 (red / orange / gray / blue)
+    prepare: Callable[[Any], Prepared] = nothing_to_prepare  # 엑셀 전체에서 작업 정보 읽기
+
+    def rank(self, label: str) -> int:
+        """중요한 대상일수록 작은 값. choices에 없는 배지 글자는 맨 뒤다."""
+        return self.choices.index(label) if label in self.choices else len(self.choices)
+
+    def priority(self, finding: Finding) -> int:
+        return self.rank(self.label(finding))
+
+    def color(self, label: str) -> str:
+        return self.colors.get(label, "gray")
diff --git a/src/llm_lab/kinds/rte.py b/src/llm_lab/kinds/rte.py
new file mode 100644
index 0000000..6fd9db2
--- /dev/null
+++ b/src/llm_lab/kinds/rte.py
@@ -0,0 +1,50 @@
+"""RTE(런타임 오류) 결과 시트: Polyspace Code Prover의 Red·Orange 검사."""
+
+from __future__ import annotations
+
+from typing import Any
+
+from llm_lab.kinds.base import Cell, Kind
+from llm_lab.polyspace import Finding, cell_text, line_number, norm
+
+# TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
+TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}
+
+
+def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
+    """Red·Orange Check면 Finding, 그 밖(Gray Check 등)이면 TYPE 글자가 뺀 이유다."""
+    color = TARGET_TYPES.get(norm(cell("type")))
+    if color is None:
+        return cell_text(cell("type")) or "(빈 TYPE)"
+    return Finding(
+        row=row,
+        color=color,
+        type=cell_text(cell("type")),
+        file=cell_text(cell("file")),
+        line=line_number(cell("line")),
+        check=cell_text(cell("check")),
+        detail=cell_text(cell("detail")),
+        id=cell_text(cell("id")),
+        group=cell_text(cell("group")),
+        information=cell_text(cell("information")),
+        function=cell_text(cell("function")),
+        status=cell_text(cell("status")),
+        comment=cell_text(cell("comment")),
+    )
+
+
+def color_of(finding: Finding) -> str:
+    return finding.color
+
+
+RTE = Kind(
+    name="rte",
+    title="RTE",
+    sheet_word="rte",
+    required=("type", "file", "line", "check", "detail"),
+    read_row=read_row,
+    label=color_of,
+    choices=("Red", "Orange"),
+    defaults=("Red", "Orange"),
+    colors={"Red": "red", "Orange": "orange"},
+)
diff --git a/src/llm_lab/polyspace.py b/src/llm_lab/polyspace.py
index 5deea28..2627854 100644
--- a/src/llm_lab/polyspace.py
+++ b/src/llm_lab/polyspace.py
@@ -1,28 +1,28 @@
-"""Polyspace 결과 엑셀에서 RTE(런타임 오류) 행을 읽는다."""
+"""Polyspace 결과 엑셀에서 고른 종류(llm_lab.kinds)의 결과 시트를 읽는다."""
 
 from __future__ import annotations
 
+from collections.abc import Callable, Collection, Iterator
 from dataclasses import dataclass
-from typing import IO, Any
+from typing import IO, TYPE_CHECKING, Any
 
 from openpyxl import load_workbook
 
-REQUIRED_COLUMNS = ("type", "file", "line", "check", "detail")
-OPTIONAL_COLUMNS = ("id", "group", "information", "function", "status", "comment")
+if TYPE_CHECKING:
+    from llm_lab.kinds import Kind
+
 # 헤더 위에 제목·요약 행이 있어도 찾도록 위에서부터 이만큼 훑는다
 HEADER_SCAN_ROWS = 30
-# TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
-TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}
 
 
 class SheetError(Exception):
-    """RTE 시트나 필수 열을 찾지 못함."""
+    """고른 종류의 시트나 필수 열을 찾지 못함."""
 
 
 @dataclass(frozen=True)
 class Finding:
     row: int  # 엑셀 행 번호 (1부터)
-    color: str  # "Red" | "Orange"
+    color: str  # RTE의 "Red" | "Orange". 다른 종류는 ""
     type: str  # TYPE 칸 원문
     file: str
     line: int | None  # 정수가 아니면 None
@@ -34,114 +34,134 @@ class Finding:
     function: str = ""
     status: str = ""
     comment: str = ""
+    # 종류별 값. 기본값이 있어야 DB에 저장된 예전 행 JSON도 그대로 읽힌다.
+    category: str = ""  # MISRA의 실제 분류 (조건 시트 우선)
+    note: str = ""  # 결과 카드에 보일 짧은 메모
+    threshold: str = ""  # CodeMetrics: Threshold 칸 원문
+    actual: str = ""  # CodeMetrics: Actual Value 칸 원문
+    stage: str = ""  # CodeMetrics: 조건 시트의 판정 글
+
+
+@dataclass(frozen=True)
+class Prepared:
+    """종류가 엑셀 전체에서 읽은 작업 정보 (조건 시트 등).
+
+    data는 JSON으로 바꿀 수 있어야 한다. 작업에 저장해 두고 행 읽기와 LLM 메시지에 넘긴다.
+    """
+
+    data: Any = None
+    note: str = ""  # 작업 메모에 남길 한 줄
 
 
 @dataclass(frozen=True)
-class RteSheet:
+class Sheet:
     sheet: str
     findings: list[Finding]
-    skipped: int  # Gray Check 등 대상이 아니어서 뺀 행 수
+    skipped: dict[str, int]  # 뺀 이유 → 행 수 (처음 나온 순서)
+    prepared: Prepared = Prepared()
 
 
-def _norm(value: Any) -> str:
+def norm(value: Any) -> str:
     """비교용: 공백을 한 칸으로 줄이고 대소문자를 무시한다."""
     return " ".join(str(value).split()).casefold() if value is not None else ""
 
 
-def _text(value: Any) -> str:
+def column_name(value: Any) -> str:
+    """헤더 비교용: norm에 더해 끝의 콜론을 뗀다 ("Review Scope:")."""
+    return norm(value).rstrip(":").strip()
+
+
+def cell_text(value: Any) -> str:
     return str(value).strip() if value is not None else ""
 
 
-def _line(value: Any) -> int | None:
+def line_number(value: Any) -> int | None:
     if isinstance(value, bool):
         return None
     if isinstance(value, int):
         return value
     if isinstance(value, float) and value.is_integer():
         return int(value)
-    text = _text(value)
+    text = cell_text(value)
     return int(text) if text.isdigit() else None
 
 
-def _header(cells: tuple[Any, ...]) -> dict[str, int] | None:
-    """필수 열이 모두 있으면 {열 이름: 위치}를, 아니면 None을 돌려준다."""
-    columns: dict[str, int] = {}
-    for index, cell in enumerate(cells):
-        columns.setdefault(_norm(cell), index)
-    return columns if all(name in columns for name in REQUIRED_COLUMNS) else None
-
-
-def _scan(sheet: Any) -> tuple[int, dict[str, int]] | str:
+def find_header(sheet: Any, required: Collection[str]) -> tuple[int, dict[str, int]] | str:
     """(헤더 행 번호, 열 위치)를, 못 찾으면 오류 안내용으로 처음 보이는 행 내용을 돌려준다."""
     first_seen = ""
     rows = sheet.iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True)
     for number, cells in enumerate(rows, start=1):
-        columns = _header(cells)
-        if columns is not None:
+        columns: dict[str, int] = {}
+        for index, cell in enumerate(cells):
+            columns.setdefault(column_name(cell), index)
+        if all(name in columns for name in required):
             return number, columns
         if not first_seen and any(cell is not None for cell in cells):
-            first_seen = ", ".join(_text(cell) for cell in cells if cell is not None)
+            first_seen = ", ".join(cell_text(cell) for cell in cells if cell is not None)
     return first_seen or "(비어 있음)"
 
 
-def read_rte(source: IO[bytes]) -> RteSheet:
-    """엑셀에서 RTE 시트를 찾아 Red/Orange 행을 읽는다."""
+def data_rows(
+    sheet: Any, header_row: int, columns: dict[str, int]
+) -> Iterator[tuple[int, Callable[[str], Any]]]:
+    """헤더 아래의 (엑셀 행 번호, 열 이름 → 칸 값). 모든 칸이 빈 행은 건너뛴다."""
+    rows = sheet.iter_rows(min_row=header_row + 1, values_only=True)
+    for number, cells in enumerate(rows, start=header_row + 1):
+        if all(cell_text(cell) == "" for cell in cells):
+            continue
+
+        def cell(name: str, cells: tuple[Any, ...] = cells) -> Any:
+            index = columns.get(name)
+            return cells[index] if index is not None and index < len(cells) else None
+
+        yield number, cell
+
+
+def read_sheet(source: IO[bytes], kind: Kind, selected: Collection[str]) -> Sheet:
+    """엑셀에서 kind의 결과 시트를 찾아 분석할 행을 읽는다. selected는 고른 대상(배지 글자)이다."""
     try:
         workbook = load_workbook(source, read_only=True, data_only=True)
     except Exception as exc:
         raise SheetError(f"엑셀(.xlsx) 파일로 읽을 수 없습니다: {exc}") from exc
     try:
-        found: list[tuple[Any, int, dict[str, int]]] = []
         notes: list[str] = []
         for sheet in workbook.worksheets:
-            if not sheet.title.casefold().endswith("_result"):
+            title = sheet.title.casefold()
+            if not title.endswith("_result"):
                 notes.append(f"{sheet.title} (이름이 _Result로 끝나지 않음)")
-                continue
-            scanned = _scan(sheet)
-            if isinstance(scanned, str):
-                notes.append(f"{sheet.title} (찾은 헤더: {scanned})")
+            elif kind.sheet_word not in title:
+                notes.append(f"{sheet.title} (이름에 {kind.sheet_word}가 없음)")
+            elif isinstance(found := find_header(sheet, kind.required), str):
+                notes.append(f"{sheet.title} (찾은 헤더: {found})")
             else:
-                found.append((sheet, *scanned))
-        if not found:
-            raise SheetError(
-                "RTE 시트를 찾지 못했습니다. 이름이 _Result로 끝나고 "
-                f"{', '.join(REQUIRED_COLUMNS)} 열이 있는 시트가 필요합니다. "
-                f"시트: {'; '.join(notes) or '없음'}"
-            )
-        sheet, header_row, columns = next(
-            (item for item in found if "rte" in item[0].title.casefold()), found[0]
+                return _read_rows(sheet, *found, kind, selected, kind.prepare(workbook))
+        raise SheetError(
+            f"{kind.title} 시트를 찾지 못했습니다. 이름이 _Result로 끝나고 {kind.sheet_word}가 "
+            f"들어가며 {', '.join(kind.required)} 열이 있는 시트가 필요합니다. "
+            f"시트: {'; '.join(notes) or '없음'}"
         )
-        return _read_rows(sheet, header_row, columns)
     finally:
         workbook.close()
 
 
-def _read_rows(sheet: Any, header_row: int, columns: dict[str, int]) -> RteSheet:
+def _read_rows(
+    sheet: Any,
+    header_row: int,
+    columns: dict[str, int],
+    kind: Kind,
+    selected: Collection[str],
+    prepared: Prepared,
+) -> Sheet:
     findings: list[Finding] = []
-    skipped = 0
-    rows = sheet.iter_rows(min_row=header_row + 1, values_only=True)
-    for number, cells in enumerate(rows, start=header_row + 1):
-        if all(_text(cell) == "" for cell in cells):
-            continue
-
-        def cell(name: str, cells: tuple[Any, ...] = cells) -> Any:
-            index = columns.get(name)
-            return cells[index] if index is not None and index < len(cells) else None
-
-        color = TARGET_TYPES.get(_norm(cell("type")))
-        if color is None:
-            skipped += 1
-            continue
-        findings.append(
-            Finding(
-                row=number,
-                color=color,
-                type=_text(cell("type")),
-                file=_text(cell("file")),
-                line=_line(cell("line")),
-                check=_text(cell("check")),
-                detail=_text(cell("detail")),
-                **{name: _text(cell(name)) for name in OPTIONAL_COLUMNS},
-            )
-        )
-    return RteSheet(sheet=sheet.title, findings=findings, skipped=skipped)
+    skipped: dict[str, int] = {}
+    for number, cell in data_rows(sheet, header_row, columns):
+        found = kind.read_row(cell, number, prepared.data)
+        if isinstance(found, Finding):
+            label = kind.label(found)
+            if label in kind.choices and label not in selected:
+                found = f"{label} (고르지 않음)"
+        if isinstance(found, str):
+            skipped[found] = skipped.get(found, 0) + 1
+        else:
+            findings.append(found)
+    return Sheet(sheet.title, findings, skipped, prepared)
diff --git a/src/llm_lab/web.py b/src/llm_lab/web.py
index 348c7fe..bfb67d8 100644
--- a/src/llm_lab/web.py
+++ b/src/llm_lab/web.py
@@ -21,9 +21,10 @@ from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Resp
 from llm_lab.client import get_client
 from llm_lab.config import ConfigError, load_settings
 from llm_lab.folder_picker import PickerError, pick_folder
+from llm_lab.kinds import RTE
 from llm_lab.pages import error_page, index_page, job_page, results_page
 from llm_lab.patch import FilePatch, PatchError, base_folder, build_patch, relative_path
-from llm_lab.polyspace import Finding, SheetError, read_rte
+from llm_lab.polyspace import Finding, SheetError, read_sheet
 from llm_lab.sources import Match, load_from_folder
 from llm_lab.store import Job, NewRow, Row, Store, StoreError
 from llm_lab.worker import Worker, daemon_pool
@@ -120,7 +121,7 @@ def create_app(store: Store, worker: Worker) -> FastAPI:
             ) + " 탐색기 주소창의 경로를 그대로 붙여넣으면 됩니다."
             return HTMLResponse(error_page("소스 폴더를 찾을 수 없음", message), status_code=400)
         try:
-            sheet = read_rte(io.BytesIO(excel.file.read()))
+            sheet = read_sheet(io.BytesIO(excel.file.read()), RTE, RTE.defaults)
         except SheetError as exc:
             return HTMLResponse(error_page("엑셀을 읽을 수 없음", str(exc)), status_code=400)
         # 엑셀에 나온 파일만 폴더에서 찾아 읽는다. 읽은 내용은 작업에 함께 저장해
@@ -129,8 +130,9 @@ def create_app(store: Store, worker: Worker) -> FastAPI:
         rows = [_new_row(finding, matches[finding.file]) for finding in sheet.findings]
         used = {row.file_name for row in rows if row.file_name}
         files = [source for source in files if source.name in used]
+        skipped = sum(sheet.skipped.values())
         job_id = store.create_job(
-            excel.filename or "", sheet.sheet, sheet.skipped, files, rows, source_root=str(root)
+            excel.filename or "", sheet.sheet, skipped, files, rows, source_root=str(root)
         )
         worker.start(job_id)
         return RedirectResponse(f"/results/{job_id}", status_code=303)
````

- [ ] **Step 4: 통과와 린트 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `316 passed`, 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/polyspace.py src/llm_lab/kinds src/llm_lab/web.py tests/test_polyspace.py tests/test_kinds.py
```

```bash
git commit -m "feat: read result sheets by kind and add the RTE kind" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 묶음 요청과 공통 답 형식 (`fixer.py`, RTE 지시문, 캐시 목록)

**Files:**
- Modify: `src/llm_lab/fixer.py` (`propose_fix` → `make_request` + `propose_fixes`, `FIX_SCHEMA` 새 형식, `Request`, `cache_key(model, request)`, `read_decisions`·`share`·`edits_results`, `EditMismatch.summary`, `FixResult.from_dict`; `SYSTEM_PROMPT`·`PROMPT_VERSION`은 없앤다)
- Modify: `src/llm_lab/kinds/base.py` (지시문·답 형식 필드와 기본 함수), `src/llm_lab/kinds/rte.py` (지시문을 옮기고 마지막 줄을 바꿈)
- Modify: `src/llm_lab/store.py` (캐시가 결과 목록을 다룸), `src/llm_lab/worker.py` (새 함수로 부름, 행 하나짜리 묶음)
- Test: `tests/rte_fixtures.py`(`answer()` 더함), `tests/test_fixer.py`(새 API로 바꿈), `tests/test_store.py`(캐시)

**Interfaces:**
- Consumes: `Kind`, `RTE` (Task 1)
- Produces:
  - `fixer.FIX_SCHEMA`: `{findings: [{number, decision, reason}], edits: [{original, replacement}]}`
  - `fixer.Request(kind, findings, source, first, last, context, messages)` (frozen)
  - `fixer.build_prompt(kind, findings, source, first, last, context) -> str`
  - `fixer.make_request(kind, findings, source, context=None) -> Request` (줄 번호가 틀리면 `FixError`)
  - `fixer.cache_key(model, request) -> str`
  - `fixer.read_decisions(data, count) -> list[tuple[str, str]]`, `fixer.share(decisions, edits, diff) -> list[FixResult]`, `fixer.edits_results(data, request) -> list[FixResult]`
  - `fixer.propose_fixes(client, model, request, transcript=None) -> list[FixResult]`
  - `fixer.EditMismatch(detail, kind="locate", summary="")`와 `MISMATCH_SUMMARIES["missing"]`
  - `FixResult.from_dict(data)`
  - `Kind`에 필드 추가: `system_prompt`, `heading`, `prompt_version`(필수), `facts`(기본 `default_facts`), `prompt_tail(context, findings, source)`(기본 `no_tail`), `region(text, findings)`(기본 `first_line_region`), `answer_schema`(기본 `FIX_SCHEMA`), `to_results(data, request)`(기본 `edits_results`), `timeout`(기본 `FIX_TIMEOUT` 120.0)
  - `kinds.rte.SYSTEM_PROMPT`, `RTE.prompt_version = 4`
  - `Store.cache_get(key) -> list[FixResult] | None`, `Store.cache_put(key, results: list[FixResult])`

이 태스크의 작업자는 아직 모든 작업을 RTE로 처리한다. 작업의 종류는 Task 3에서 DB에 저장한다.

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/rte_fixtures.py b/tests/rte_fixtures.py
index 95c1c06..a7105a6 100644
--- a/tests/rte_fixtures.py
+++ b/tests/rte_fixtures.py
@@ -78,11 +78,24 @@ def finding(**overrides: object) -> Finding:
     return Finding(**{**values, **overrides})
 
 
+def answer(
+    *findings: tuple[str, str], edits: list[dict] | None = None, finish_reason: str = "stop"
+) -> httpx2.Response:
+    """LLM의 공통 답. findings는 1번부터 차례로 (판단, 이유)."""
+    body = {
+        "findings": [
+            {"number": number, "decision": decision, "reason": reason}
+            for number, (decision, reason) in enumerate(findings, start=1)
+        ],
+        "edits": edits if edits is not None else [],
+    }
+    return completion(json.dumps(body, ensure_ascii=False), finish_reason=finish_reason)
+
+
 def fix_answer(
     decision: str = "fix", edits: list[dict] | None = None, *, finish_reason: str = "stop"
 ) -> httpx2.Response:
-    """LLM의 json_schema 답변. edits를 안 주면 calc.c의 0 나누기 수정."""
+    """행 하나짜리 답. edits를 안 주면 calc.c의 0 나누기 수정."""
     if edits is None:
         edits = [DIVIDE_FIX] if decision == "fix" else []
-    body = {"decision": decision, "reason": REASON, "edits": edits}
-    return completion(json.dumps(body, ensure_ascii=False), finish_reason=finish_reason)
+    return answer((decision, REASON), edits=edits, finish_reason=finish_reason)
diff --git a/tests/test_fixer.py b/tests/test_fixer.py
index 0313644..a9a15d5 100644
--- a/tests/test_fixer.py
+++ b/tests/test_fixer.py
@@ -1,18 +1,35 @@
+import json
+from dataclasses import replace
+
 import pytest
 from fake_llm import MODEL, completion, error, make_client, scripted
-from rte_fixtures import CALC_C, REASON, finding, fix_answer
-
-from llm_lab import fixer
-from llm_lab.fixer import FatalLLMError, FixError, apply_edits, cache_key, propose_fix
+from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer
+
+from llm_lab.fixer import (
+    EditMismatch,
+    FatalLLMError,
+    FixError,
+    FixResult,
+    apply_edits,
+    cache_key,
+    make_request,
+    propose_fixes,
+)
+from llm_lab.kinds import RTE
 from llm_lab.sources import decode_source
 
 SOURCE = decode_source("calc.c", CALC_C)
 
 
+def ask(handler, findings=None, *, source=SOURCE, kind=RTE, transcript=None):
+    request = make_request(kind, findings or [finding()], source)
+    return propose_fixes(make_client(handler), MODEL, request, transcript)
+
+
 def test_fix_returns_edits_diff_and_sends_schema_and_numbered_code():
     handler, sent = scripted(fix_answer())
 
-    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+    (result,) = ask(handler)
 
     assert result.decision == "fix"
     assert result.reason == REASON
@@ -20,11 +37,14 @@ def test_fix_returns_edits_diff_and_sends_schema_and_numbered_code():
     assert "+    return (b != 0) ? a / b : 0;" in result.diff
     request = sent[0]
     assert request["model"] == MODEL
-    assert request["response_format"]["json_schema"]["name"] == "rte_fix"
-    assert request["messages"][0]["role"] == "system"
+    schema = request["response_format"]["json_schema"]
+    assert schema["name"] == "polyspace_fix"
+    assert schema["schema"]["required"] == ["findings", "edits"]
+    assert request["messages"][0] == {"role": "system", "content": RTE.system_prompt}
     prompt = request["messages"][1]["content"]
+    assert prompt.startswith("Polyspace Code Prover result:\n\nFinding 1:\n- TYPE: Orange Check\n")
     assert "- check: Division by zero" in prompt
-    assert "- line: 3" in prompt
+    assert "- File: calc.c\n- line: 3\n\nCode (lines 1-4 of calc.c;" in prompt
     assert "    3|     return a / b;" in prompt
     assert "max_tokens" not in request  # 생각 토큰 때문에 출력 한도를 걸지 않는다
 
@@ -32,16 +52,55 @@ def test_fix_returns_edits_diff_and_sends_schema_and_numbered_code():
 def test_no_fix_ignores_edits():
     handler, _ = scripted(fix_answer("no_fix", edits=[{"original": "x", "replacement": "y"}]))
 
-    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+    (result,) = ask(handler)
 
     assert (result.decision, result.edits, result.diff) == ("no_fix", (), "")
 
 
+def test_group_gets_a_decision_per_finding_and_shares_the_edits():
+    findings = [finding(), finding(row=3, check="Overflow")]
+    handler, sent = scripted(
+        answer(("no_fix", "범위가 보장됨"), ("fix", REASON), edits=[DIVIDE_FIX])
+    )
+
+    first, second = ask(handler, findings)
+
+    assert (first.decision, first.reason, first.edits, first.diff) == (
+        "no_fix",
+        "범위가 보장됨",
+        (),
+        "",
+    )
+    assert (second.decision, second.reason) == ("fix", REASON)
+    assert "(b != 0) ? a / b : 0;" in apply_edits(SOURCE.text, second.edits)
+    prompt = sent[0]["messages"][1]["content"]
+    assert "Finding 1:\n- TYPE: Orange Check\n- check: Division by zero\n" in prompt
+    assert "Finding 2:\n- TYPE: Orange Check\n- check: Overflow\n" in prompt
+    assert len(sent) == 1
+
+
+@pytest.mark.parametrize("numbers", [[1], [1, 1], [1, 3], [2, 1, 3]])
+def test_findings_must_answer_each_number_once(numbers):
+    body = {
+        "findings": [{"number": n, "decision": "no_fix", "reason": "r"} for n in numbers],
+        "edits": [],
+    }
+    handler, sent = scripted(completion(json.dumps(body)), completion(json.dumps(body)))
+
+    with pytest.raises(FixError) as caught:
+        ask(handler, [finding(), finding(row=3)])
+
+    first, _, more = str(caught.value).split("\n")
+    assert first == "LLM 답에 빠진 행이 있습니다."
+    assert "1부터 2까지 한 번씩" in more
+    assert "findings의 number" in sent[1]["messages"][3]["content"]
+
+
 def test_unusable_answer_is_retried_once_with_the_problem():
     wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
     handler, sent = scripted(wrong, fix_answer())
 
-    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+    (result,) = ask(handler)
 
     assert result.decision == "fix"
     retry = sent[1]["messages"]
@@ -55,7 +114,7 @@ def test_replacement_not_writable_in_source_encoding_is_retried():
     dash = {"original": "    return a / b;", "replacement": "    return b ? a / b : 0; /* b – 0 */"}
     handler, sent = scripted(fix_answer(edits=[dash]), fix_answer())
 
-    result = propose_fix(make_client(handler), MODEL, finding(line=4), source)
+    (result,) = ask(handler, [finding(line=4)], source=source)
 
     assert result.decision == "fix"
     assert "–" not in apply_edits(source.text, result.edits)
@@ -67,7 +126,7 @@ def test_transcript_records_each_request_and_response():
     handler, _ = scripted(wrong, fix_answer())
     transcript: list = []
 
-    propose_fix(make_client(handler), MODEL, finding(), SOURCE, transcript)
+    ask(handler, transcript=transcript)
 
     first, second = transcript
     assert first.messages[0]["role"] == "system"
@@ -84,7 +143,7 @@ def test_transcript_records_api_errors():
     transcript: list = []
 
     with pytest.raises(FatalLLMError):
-        propose_fix(make_client(handler), MODEL, finding(), SOURCE, transcript)
+        ask(handler, transcript=transcript)
 
     assert "[인증]" in transcript[0].error
     assert transcript[0].raw is None
@@ -94,6 +153,8 @@ def test_transcript_records_api_errors():
     ("bad", "summary", "detail"),
     [
         (completion("not json"), "LLM 답을 읽을 수 없었습니다.", "JSON이 아님"),
+        (completion("[]"), "LLM 답을 읽을 수 없었습니다.", "JSON 객체가 아님"),
+        (completion('{"findings": "x"}'), "LLM 답을 읽을 수 없었습니다.", "스키마와 다름"),
         (fix_answer(finish_reason="length"), "LLM 답이 길이 제한에 걸려 잘렸습니다.", "잘림"),
         (fix_answer(edits=[]), "LLM 답을 읽을 수 없었습니다.", "edits가 비었거나"),
         (
@@ -107,7 +168,7 @@ def test_gives_up_after_two_unusable_answers_with_a_plain_message(bad, summary,
     handler, sent = scripted(bad, bad)
 
     with pytest.raises(FixError) as caught:
-        propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+        ask(handler)
 
     # 화면용: 첫 줄은 무엇이 문제인지, 다음 줄은 할 일, 개발자용 내용은 "자세히:" 뒤에
     first, todo, more = str(caught.value).split("\n")
@@ -123,53 +184,103 @@ def test_repeated_code_is_resolved_with_the_reported_line():
     edit = {"original": "    a = a + 1;", "replacement": "    a = inc(a);"}
     handler, sent = scripted(fix_answer(edits=[edit]))
 
-    result = propose_fix(make_client(handler), MODEL, finding(line=4), source)
+    (result,) = ask(handler, [finding(line=4)], source=source)
 
     assert apply_edits(source.text, result.edits).endswith("    a = a + 1;\n    a = inc(a);\n}\n")
     assert len(sent) == 1
 
 
-def test_prompt_asks_for_small_edits():
-    assert "only the lines you change" in fixer.SYSTEM_PROMPT
+def test_rte_prompt_asks_for_small_edits_and_the_common_answer():
+    assert "only the lines you change" in RTE.system_prompt
+    assert '"findings" has one entry for each numbered finding' in RTE.system_prompt
 
 
 def test_auth_error_is_fatal_and_not_retried():
     handler, sent = scripted(error(401, "invalid key"))
 
     with pytest.raises(FatalLLMError, match="인증"):
-        propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+        ask(handler)
     assert len(sent) == 1
 
 
-def test_other_api_error_fails_only_this_row():
+def test_other_api_error_fails_only_this_group():
     handler, _ = scripted(error(400, "response_format not supported"))
 
     with pytest.raises(FixError) as caught:
-        propose_fix(make_client(handler), MODEL, finding(), SOURCE)
+        ask(handler)
 
     message = str(caught.value)
     assert message.startswith("LLM 서버가 요청을 처리하지 못했습니다.\n할 일: ")
     assert "기능 미지원" in message
 
 
-@pytest.mark.parametrize(("line", "message"), [(None, "정수가 아님"), (9, "9번 줄이 없음")])
-def test_bad_line_fails_without_calling_llm(line, message):
-    handler, sent = scripted()
-
+@pytest.mark.parametrize(
+    ("line", "message"), [(None, "line 칸이 정수가 아님"), (9, "9번 줄이 없음")]
+)
+def test_bad_line_is_found_before_calling_llm(line, message):
     with pytest.raises(FixError) as caught:
-        propose_fix(make_client(handler), MODEL, finding(line=line), SOURCE)
+        make_request(RTE, [finding(), finding(row=4, line=line)], SOURCE)
 
     assert str(caught.value).startswith("엑셀의 줄 번호가 소스와 맞지 않습니다.\n할 일: ")
-    assert message in str(caught.value)
-    assert sent == []
+    assert f"엑셀 4행: {message}" in str(caught.value)
+
+
+def test_cache_key_depends_on_the_messages_file_and_kind():
+    def key(findings=None, *, source=SOURCE, kind=RTE, model=MODEL):
+        return cache_key(model, make_request(kind, findings or [finding()], source))
+
+    assert key() == key([finding(row=99)])  # 엑셀 행 번호는 상관없다
+    assert key() != key(source=decode_source("calc.c", CALC_C + b"int x;\n"))
+    assert key() != key([finding(line=2)])
+    assert key() != key([finding(), finding(row=3)])
+    assert key() != key(model="other-model")
+    assert key() != key(kind=replace(RTE, prompt_version=RTE.prompt_version + 1))
+    assert key() != key(kind=replace(RTE, system_prompt=RTE.system_prompt + "Be brief.\n"))
+
+
+def test_kind_can_add_a_message_tail_its_own_answer_format_and_timeout():
+    schema = {
+        "type": "object",
+        "properties": {"verdict": {"type": "string"}},
+        "required": ["verdict"],
+        "additionalProperties": False,
+    }
+
+    def tail(context, findings, source):
+        return f"Limit {context['limit']} for {len(findings)} finding(s) in {source.name}."
+
+    def to_results(data, request):
+        assert request.context == {"limit": 10}
+        return [FixResult("no_fix", data["verdict"], (), "")]
 
+    kind = replace(
+        RTE, prompt_tail=tail, answer_schema=schema, to_results=to_results, timeout=300.0
+    )
+    timeouts = []
 
-def test_cache_key_depends_on_file_content_finding_and_prompt_version(monkeypatch):
-    key = cache_key(MODEL, SOURCE, finding())
+    def handler(request):
+        timeouts.append(request.extensions["timeout"]["read"])
+        return completion('{"verdict": "그대로 둠"}')
+
+    request = make_request(kind, [finding()], SOURCE, {"limit": 10})
+    (result,) = propose_fixes(make_client(handler), MODEL, request)
+
+    assert result.reason == "그대로 둠"
+    prompt = request.messages[1]["content"]
+    assert "- line: 3\n\nLimit 10 for 1 finding(s) in calc.c.\n\nCode (lines" in prompt
+    assert timeouts == [300.0]
+
+
+def test_kind_answer_check_can_name_its_own_problem():
+    def to_results(data, request):
+        raise EditMismatch("함수 시그니처가 바뀜", summary="LLM이 함수 이름이나 인자를 바꿨습니다.")
+
+    handler, sent = scripted(fix_answer(), fix_answer())
+
+    with pytest.raises(FixError) as caught:
+        ask(handler, kind=replace(RTE, to_results=to_results))
 
-    assert key == cache_key(MODEL, SOURCE, finding(row=99))  # 엑셀 행 번호는 상관없다
-    assert key != cache_key(MODEL, decode_source("calc.c", b"int x;\n"), finding())
-    assert key != cache_key(MODEL, SOURCE, finding(line=2))
-    assert key != cache_key("other-model", SOURCE, finding())
-    monkeypatch.setattr(fixer, "PROMPT_VERSION", fixer.PROMPT_VERSION + 1)
-    assert key != cache_key(MODEL, SOURCE, finding())
+    first, _, more = str(caught.value).split("\n")
+    assert first == "LLM이 함수 이름이나 인자를 바꿨습니다."
+    assert more == "자세히: 함수 시그니처가 바뀜 (2번 시도)"
+    assert "함수 시그니처가 바뀜" in sent[1]["messages"][3]["content"]
diff --git a/tests/test_store.py b/tests/test_store.py
index 5e582b8..c916222 100644
--- a/tests/test_store.py
+++ b/tests/test_store.py
@@ -94,12 +94,22 @@ def test_recover_returns_running_jobs_and_requeues_running_rows(tmp_path):
     assert done not in store.recover()
 
 
-def test_cache_round_trip(tmp_path):
+def test_cache_keeps_the_results_of_a_group_in_order(tmp_path):
     store = Store(tmp_path / "fixer.db")
+    results = [RESULT, FixResult("no_fix", "범위가 보장됨", (), "")]
+
+    assert store.cache_get("k") is None
+    store.cache_put("k", results)
+    assert store.cache_get("k") == results
+
+
+def test_cache_entry_from_before_groups_is_ignored(tmp_path):
+    store = Store(tmp_path / "fixer.db")
+    with closing(sqlite3.connect(tmp_path / "fixer.db")) as db:  # 결과 하나를 그대로 저장하던 때
+        db.execute("INSERT INTO cache VALUES ('k', ?, '2026-10-07T00:00:00')", (RESULT.to_json(),))
+        db.commit()
 
     assert store.cache_get("k") is None
-    store.cache_put("k", RESULT)
-    assert store.cache_get("k") == RESULT
 
 
 def test_jobs_lists_newest_first(tmp_path):
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q`
Expected: `ERROR tests/test_fixer.py` — `ImportError: cannot import name 'make_request' from 'llm_lab.fixer'`

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/fixer.py b/src/llm_lab/fixer.py
index 0b333fc..8c5a3de 100644
--- a/src/llm_lab/fixer.py
+++ b/src/llm_lab/fixer.py
@@ -1,4 +1,5 @@
-"""엔진: RTE 행 하나를 LLM에 보내 수정 / 수정 불필요 판단과 수정안을 받는다."""
+"""엔진: 묶음 하나(함께 보낼 행들)를 LLM에 보내 행마다 수정 / 수정 불필요 판단과
+그 묶음이 함께 쓸 수정안을 받는다."""
 
 from __future__ import annotations
 
@@ -8,7 +9,7 @@ import json
 import re
 import time
 from dataclasses import asdict, dataclass
-from typing import Any
+from typing import TYPE_CHECKING, Any
 
 import openai
 from openai import OpenAI
@@ -17,6 +18,9 @@ from llm_lab.polyspace import Finding
 from llm_lab.probe import FATAL_CATEGORIES, classify_error
 from llm_lab.sources import SourceFile
 
+if TYPE_CHECKING:
+    from llm_lab.kinds import Kind
+
 # 이 줄 수 이하면 파일 전체를 보낸다. 2,000줄은 대략 2만~3만 토큰으로, `llm-probe --context`로
 # 잰 컨텍스트 길이에 넉넉히 들어간다. 더 긴 파일은 지적된 줄이 든 함수만 보낸다.
 WHOLE_FILE_MAX_LINES = 2000
@@ -24,10 +28,8 @@ WHOLE_FILE_MAX_LINES = 2000
 WINDOW_LINES = 60
 # 함수 시그니처를 넣으려고 '{' 위로 거슬러 올라가는 최대 줄 수
 SIGNATURE_LINES = 10
+# 종류가 따로 정하지 않으면 쓰는 LLM 대기 시간(초)
 FIX_TIMEOUT = 120.0
-# 프롬프트나 응답 처리 방식을 바꾸면 올린다 (캐시 키에 들어간다)
-# 2: 파일 전체를 보내는 기준 400 → 2,000줄, 3: 바꾸는 줄만 짧게 보내라는 지시
-PROMPT_VERSION = 3
 
 
 class FixError(Exception):
@@ -39,14 +41,16 @@ class FatalLLMError(Exception):
 
 
 class EditMismatch(Exception):
-    """LLM이 준 수정을 원본에 적용할 수 없음 (LLM에 다시 요청할 사유).
+    """LLM 답을 쓸 수 없음 (LLM에 다시 요청할 사유).
 
-    kind는 화면에 보일 설명을 고르는 데 쓴다: locate / format / truncated / encoding
+    kind는 화면에 보일 설명을 고르는 데 쓴다: locate / format / truncated / encoding / missing.
+    summary를 주면 그 글을 설명으로 쓴다 (종류가 자기 검사에 맞는 설명을 붙일 때).
     """
 
-    def __init__(self, detail: str, kind: str = "locate") -> None:
+    def __init__(self, detail: str, kind: str = "locate", summary: str = "") -> None:
         super().__init__(detail)
         self.kind = kind
+        self.summary = summary or MISMATCH_SUMMARIES[kind]
 
 
 # 두 번 물어도 쓸 수 없는 답이었을 때 화면에 보일 설명 (kind별)
@@ -55,6 +59,7 @@ MISMATCH_SUMMARIES = {
     "format": "LLM 답을 읽을 수 없었습니다.",
     "truncated": "LLM 답이 길이 제한에 걸려 잘렸습니다.",
     "encoding": "LLM이 이 파일의 인코딩으로 쓸 수 없는 문자를 썼습니다.",
+    "missing": "LLM 답에 빠진 행이 있습니다.",
 }
 RETRY_ACTION = "'오류 행 다시 시도'를 누르세요. 그래도 같으면 대화 기록을 보고 직접 고치세요."
 
@@ -86,7 +91,10 @@ class FixResult:
 
     @classmethod
     def from_json(cls, text: str) -> FixResult:
-        data = json.loads(text)
+        return cls.from_dict(json.loads(text))
+
+    @classmethod
+    def from_dict(cls, data: dict[str, Any]) -> FixResult:
         edits = tuple(Edit(**edit) for edit in data["edits"])
         return cls(data["decision"], data["reason"], edits, data["diff"])
 
@@ -344,34 +352,23 @@ def display_diff(name: str, old: str, new: str) -> str:
     return "\n".join(lines)
 
 
-SYSTEM_PROMPT = """\
-You review one Polyspace Code Prover run-time check in C code \
-and decide whether the code must change.
-- Red Check: Polyspace proved that the operation fails whenever it runs. Fix it.
-- Orange Check: Polyspace could not prove the operation safe. \
-Fix it if some execution can really fail. \
-If the code shown guarantees safety, answer no_fix and name the lines that guarantee it.
-When you fix:
-- Make the safety provable by static analysis: explicit range checks, \
-division-by-zero checks, initialization and similar.
-- Change as little as possible. Do not touch lines unrelated to the check. \
-Keep the behavior for valid inputs.
-- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
-without the line-number prefix. "replacement" is the new text for those lines.
-- Keep each edit small: "original" holds only the lines you change, not the whole function. \
-Add one unchanged neighbouring line only when the changed line appears more than once.
-Do not guess definitions (macros, types, other functions) that are not shown. \
-If they matter, say so in reason.
-Write "reason" in Korean, one to three sentences that a reviewer can paste \
-into a Polyspace comment.
-Answer with JSON only. For no_fix, "edits" is [].
-"""
-
+# 공통 답 형식: 판단과 이유는 행마다, 수정(edits)은 묶음이 함께 쓴다
 FIX_SCHEMA = {
     "type": "object",
     "properties": {
-        "decision": {"type": "string", "enum": ["fix", "no_fix"]},
-        "reason": {"type": "string"},
+        "findings": {
+            "type": "array",
+            "items": {
+                "type": "object",
+                "properties": {
+                    "number": {"type": "integer"},
+                    "decision": {"type": "string", "enum": ["fix", "no_fix"]},
+                    "reason": {"type": "string"},
+                },
+                "required": ["number", "decision", "reason"],
+                "additionalProperties": False,
+            },
+        },
         "edits": {
             "type": "array",
             "items": {
@@ -385,43 +382,83 @@ FIX_SCHEMA = {
             },
         },
     },
-    "required": ["decision", "reason", "edits"],
+    "required": ["findings", "edits"],
     "additionalProperties": False,
 }
 
 
-def build_prompt(finding: Finding, source: SourceFile, first: int, last: int) -> str:
-    facts = [
-        ("TYPE", finding.type),
-        ("check", finding.check),
-        ("detail", finding.detail),
-        ("Group", finding.group),
-        ("information", finding.information),
-        ("Function", finding.function),
-        ("File", source.name),
-        ("line", str(finding.line)),
-    ]
-    listed = "\n".join(f"- {name}: {value}" for name, value in facts if value)
+@dataclass(frozen=True)
+class Request:
+    """LLM에 보낼 요청 하나 (묶음 하나)."""
+
+    kind: Kind
+    findings: list[Finding]  # 메시지의 Finding 1, 2 … 순서
+    source: SourceFile
+    first: int  # 보낸 코드 범위 (1부터, 양 끝 포함)
+    last: int
+    context: Any  # 작업 정보 (polyspace.Prepared.data)
+    messages: list[dict[str, str]]
+
+
+def build_prompt(
+    kind: Kind, findings: list[Finding], source: SourceFile, first: int, last: int, context: Any
+) -> str:
+    blocks = []
+    for number, finding in enumerate(findings, start=1):
+        facts = [*kind.facts(finding), ("File", source.name), ("line", str(finding.line))]
+        listed = "\n".join(f"- {name}: {value}" for name, value in facts if value)
+        blocks.append(f"Finding {number}:\n{listed}")
+    tail = kind.prompt_tail(context, findings, source)
     return (
-        f"Polyspace Code Prover result:\n{listed}\n\n"
-        f"Code (lines {first}-{last} of {source.name}; "
+        f"{kind.heading}:\n\n"
+        + "\n\n".join(blocks)
+        + (f"\n\n{tail}" if tail else "")
+        + f"\n\nCode (lines {first}-{last} of {source.name}; "
         "each line starts with its number and '| '):\n"
         f"```c\n{numbered(source.text, first, last)}\n```"
     )
 
 
-def cache_key(model: str, source: SourceFile, finding: Finding) -> str:
+def make_request(
+    kind: Kind, findings: list[Finding], source: SourceFile, context: Any = None
+) -> Request:
+    """묶음의 줄 번호를 확인하고 보낼 메시지를 만든다. 줄 번호가 소스와 맞지 않으면 FixError."""
+    line_count = len(split_lines(source.text))
+    for finding in findings:
+        line = finding.line
+        if line is None or not 1 <= line <= line_count:
+            problem = (
+                "line 칸이 정수가 아님"
+                if line is None
+                else f"{line}번 줄이 없음 (파일은 {line_count}줄)"
+            )
+            raise FixError(
+                user_error(
+                    "엑셀의 줄 번호가 소스와 맞지 않습니다.",
+                    "엑셀을 만든 것과 같은 버전의 소스 폴더를 골라 다시 분석하세요.",
+                    f"엑셀 {finding.row}행: {problem}",
+                )
+            )
+    first, last = kind.region(source.text, findings)
+    messages = [
+        {"role": "system", "content": kind.system_prompt},
+        {"role": "user", "content": build_prompt(kind, findings, source, first, last, context)},
+    ]
+    return Request(kind, list(findings), source, first, last, context, messages)
+
+
+def cache_key(model: str, request: Request) -> str:
+    """보내는 메시지와 원본 파일이 같으면 같은 키. 엑셀 행 번호는 들어가지 않는다.
+
+    결과의 수정 위치가 파일 전체 기준이라 원본 sha256도 넣는다. 지시문 글은 메시지에 있으므로,
+    prompt_version은 답 처리 방식을 바꿀 때 올린다.
+    """
     parts = [
-        str(PROMPT_VERSION),
+        request.kind.name,
+        str(request.kind.prompt_version),
         model,
-        source.sha256,
-        finding.type,
-        finding.check,
-        finding.detail,
-        finding.group,
-        finding.information,
-        finding.function,
-        str(finding.line),
+        request.source.sha256,
+        *(message["content"] for message in request.messages),
     ]
     return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
 
@@ -444,6 +481,7 @@ def _ask(
     client: OpenAI,
     model: str,
     messages: list[dict[str, str]],
+    schema: Any,
     transcript: list[Exchange] | None,
 ) -> tuple[str, str | None]:
     """(응답 내용, finish_reason). 네트워크·SSL·인증 오류면 FatalLLMError."""
@@ -454,7 +492,7 @@ def _ask(
             messages=messages,  # type: ignore[arg-type]
             response_format={
                 "type": "json_schema",
-                "json_schema": {"name": "rte_fix", "schema": FIX_SCHEMA, "strict": True},
+                "json_schema": {"name": "polyspace_fix", "schema": schema, "strict": True},
             },
         )
     except openai.APIError as exc:
@@ -492,29 +530,54 @@ def _ask(
     return content, finish_reason
 
 
-def _to_result(
-    content: str,
-    finish_reason: str | None,
-    source: SourceFile,
-    first: int,
-    last: int,
-    line: int,
-) -> FixResult:
-    if finish_reason == "length":
-        raise EditMismatch("응답이 출력 길이 제한으로 잘림", "truncated")
-    try:
-        data: Any = json.loads(content)
-    except json.JSONDecodeError:
-        raise EditMismatch("응답이 JSON이 아님", "format") from None
-    if (
-        not isinstance(data, dict)
-        or data.get("decision") not in ("fix", "no_fix")
-        or not isinstance(data.get("reason"), str)
+def read_decisions(data: dict[str, Any], count: int) -> list[tuple[str, str]]:
+    """findings에서 번호 순서대로 (판단, 이유)를 꺼낸다. 종류별 답 처리도 이 함수를 쓴다.
+
+    번호가 1부터 count까지 한 번씩이 아니면 EditMismatch다.
+    """
+    answers = data.get("findings")
+    if not isinstance(answers, list) or not all(
+        isinstance(item, dict)
+        and type(item.get("number")) is int
+        and item.get("decision") in ("fix", "no_fix")
+        and isinstance(item.get("reason"), str)
+        for item in answers
     ):
-        raise EditMismatch("decision·reason이 스키마와 다름", "format")
-    if data["decision"] == "no_fix":
-        return FixResult("no_fix", data["reason"], (), "")
-    raw_edits = data.get("edits")
+        raise EditMismatch("findings가 스키마와 다름", "format")
+    numbers = sorted(item["number"] for item in answers)
+    if numbers != list(range(1, count + 1)):
+        raise EditMismatch(
+            f"findings의 number는 1부터 {count}까지 한 번씩이어야 함 (받은 번호: {numbers})",
+            "missing",
+        )
+    by_number = {item["number"]: item for item in answers}
+    return [(by_number[n]["decision"], by_number[n]["reason"]) for n in range(1, count + 1)]
+
+
+def share(decisions: list[tuple[str, str]], edits: tuple[Edit, ...], diff: str) -> list[FixResult]:
+    """판단마다 결과 하나. fix 행은 묶음이 함께 쓰는 수정과 diff를 갖는다."""
+    return [
+        FixResult("fix", reason, edits, diff)
+        if decision == "fix"
+        else FixResult("no_fix", reason, (), "")
+        for decision, reason in decisions
+    ]
+
+
+def edits_results(data: dict[str, Any], request: Request) -> list[FixResult]:
+    """공통 답 처리: 행별 판단과, fix가 하나라도 있으면 edits로 만든 묶음 공통 수정.
+
+    모두 no_fix면 edits는 버린다.
+    """
+    decisions = read_decisions(data, len(request.findings))
+    if all(decision == "no_fix" for decision, _ in decisions):
+        return share(decisions, (), "")
+    edits, diff = _located_edits(data.get("edits"), request)
+    return share(decisions, edits, diff)
+
+
+def _located_edits(raw_edits: Any, request: Request) -> tuple[tuple[Edit, ...], str]:
+    source = request.source
     if (
         not isinstance(raw_edits, list)
         or not raw_edits
@@ -537,53 +600,44 @@ def _to_result(
                 f"U+{char:04X}가 있음 - ASCII나 한글만 쓰세요",
                 "encoding",
             ) from None
-    edits = locate_edits(source.text, first, last, raw_edits, line)
+    line = request.findings[0].line
+    edits = locate_edits(source.text, request.first, request.last, raw_edits, line)
     fixed = apply_edits(source.text, edits)
     if fixed == source.text:
         raise EditMismatch("수정해도 원본과 같음")
-    return FixResult("fix", data["reason"], edits, display_diff(source.name, source.text, fixed))
+    return edits, display_diff(source.name, source.text, fixed)
 
 
-def propose_fix(
-    client: OpenAI,
-    model: str,
-    finding: Finding,
-    source: SourceFile,
-    transcript: list[Exchange] | None = None,
-) -> FixResult:
-    """RTE 행 하나에 대한 판단과 수정. 쓸 수 없는 응답이면 한 번만 다시 요청한다.
+def _to_results(content: str, finish_reason: str | None, request: Request) -> list[FixResult]:
+    if finish_reason == "length":
+        raise EditMismatch("응답이 출력 길이 제한으로 잘림", "truncated")
+    try:
+        data: Any = json.loads(content)
+    except json.JSONDecodeError:
+        raise EditMismatch("응답이 JSON이 아님", "format") from None
+    if not isinstance(data, dict):
+        raise EditMismatch("응답이 JSON 객체가 아님", "format")
+    return request.kind.to_results(data, request)
+
+
+def propose_fixes(
+    client: OpenAI, model: str, request: Request, transcript: list[Exchange] | None = None
+) -> list[FixResult]:
+    """묶음 하나에 대한 행별 판단과 함께 쓰는 수정. 쓸 수 없는 답이면 한 번만 다시 요청한다.
 
+    돌려주는 목록은 request.findings와 같은 순서다.
     transcript를 주면 LLM과 주고받은 요청·응답을 순서대로 담는다.
     """
-    line_count = len(split_lines(source.text))
-    line = finding.line
-    if line is None or not 1 <= line <= line_count:
-        detail = (
-            "line 칸이 정수가 아님"
-            if line is None
-            else f"{line}번 줄이 없음 (파일은 {line_count}줄)"
-        )
-        raise FixError(
-            user_error(
-                "엑셀의 줄 번호가 소스와 맞지 않습니다.",
-                "엑셀을 만든 것과 같은 버전의 소스 폴더를 골라 다시 분석하세요.",
-                detail,
-            )
-        )
-    first, last = select_region(source.text, line)
-    client = client.with_options(timeout=FIX_TIMEOUT, max_retries=1)
-    messages = [
-        {"role": "system", "content": SYSTEM_PROMPT},
-        {"role": "user", "content": build_prompt(finding, source, first, last)},
-    ]
-    problem = ""
-    kind = "locate"
+    kind = request.kind
+    client = client.with_options(timeout=kind.timeout, max_retries=1)
+    messages = list(request.messages)
+    problem, summary = "", MISMATCH_SUMMARIES["locate"]
     for _ in range(2):
-        content, finish_reason = _ask(client, model, messages, transcript)
+        content, finish_reason = _ask(client, model, messages, kind.answer_schema, transcript)
         try:
-            return _to_result(content, finish_reason, source, first, last, line)
+            return _to_results(content, finish_reason, request)
         except EditMismatch as exc:
-            problem, kind = str(exc), exc.kind
+            problem, summary = str(exc), exc.summary
             messages = [
                 *messages,
                 {"role": "assistant", "content": content},
@@ -593,4 +647,4 @@ def propose_fix(
                     "Answer again with the same JSON format.",
                 },
             ]
-    raise FixError(user_error(MISMATCH_SUMMARIES[kind], RETRY_ACTION, f"{problem} (2번 시도)"))
+    raise FixError(user_error(summary, RETRY_ACTION, f"{problem} (2번 시도)"))
diff --git a/src/llm_lab/kinds/base.py b/src/llm_lab/kinds/base.py
index 957e0d4..ffea29a 100644
--- a/src/llm_lab/kinds/base.py
+++ b/src/llm_lab/kinds/base.py
@@ -1,12 +1,21 @@
-"""시트 종류(Kind): 종류마다 다른 것(읽을 시트·열, 분석할 행, 배지)을 한곳에 모은 정의."""
+"""시트 종류(Kind): 종류마다 다른 것(읽을 시트·열, 분석할 행, 배지, LLM 지시와 답)을 모은 정의."""
 
 from __future__ import annotations
 
 from collections.abc import Callable, Mapping
-from dataclasses import dataclass
+from dataclasses import dataclass, field
 from typing import Any
 
+from llm_lab.fixer import (
+    FIX_SCHEMA,
+    FIX_TIMEOUT,
+    FixResult,
+    Request,
+    edits_results,
+    select_region,
+)
 from llm_lab.polyspace import Finding, Prepared
+from llm_lab.sources import SourceFile
 
 Cell = Callable[[str], Any]  # 열 이름 → 그 행의 칸 값 (없는 열이면 None)
 
@@ -15,6 +24,29 @@ def nothing_to_prepare(workbook: Any) -> Prepared:
     return Prepared()
 
 
+def default_facts(finding: Finding) -> list[tuple[str, str]]:
+    """사용자 메시지에 넣을 지적 정보. File과 line은 fixer가 붙인다."""
+    return [
+        ("TYPE", finding.type),
+        ("check", finding.check),
+        ("detail", finding.detail),
+        ("Group", finding.group),
+        ("information", finding.information),
+        ("Function", finding.function),
+    ]
+
+
+def no_tail(context: Any, findings: list[Finding], source: SourceFile) -> str:
+    return ""
+
+
+def first_line_region(text: str, findings: list[Finding]) -> tuple[int, int]:
+    """묶음 첫 행의 줄로 보낼 범위를 정한다 (파일 전체, 함수, 앞뒤 60줄 중 하나)."""
+    line = findings[0].line
+    assert line is not None  # make_request가 먼저 확인한다
+    return select_region(text, line)
+
+
 @dataclass(frozen=True, eq=False)
 class Kind:
     name: str  # DB와 폼에 쓰는 이름
@@ -26,7 +58,17 @@ class Kind:
     choices: tuple[str, ...]  # 첫 화면에서 고르는 대상. 중요한 것부터 적는다
     defaults: tuple[str, ...]  # 처음에 체크된 대상
     colors: Mapping[str, str]  # 배지 글자 → 배지 색 (red / orange / gray / blue)
+    system_prompt: str  # 시스템 메시지 (영어. reason만 한국어로 받는다)
+    heading: str  # 사용자 메시지 첫 줄
+    prompt_version: int  # 답 처리 방식을 바꾸면 올린다 (캐시 키)
     prepare: Callable[[Any], Prepared] = nothing_to_prepare  # 엑셀 전체에서 작업 정보 읽기
+    facts: Callable[[Finding], list[tuple[str, str]]] = default_facts
+    # 사용자 메시지에서 Finding 목록 뒤, 코드 앞에 붙일 글 (작업 정보, 묶음의 행, 소스로)
+    prompt_tail: Callable[[Any, list[Finding], SourceFile], str] = no_tail
+    region: Callable[[str, list[Finding]], tuple[int, int]] = first_line_region
+    answer_schema: Mapping[str, Any] = field(default_factory=lambda: FIX_SCHEMA)
+    to_results: Callable[[dict[str, Any], Request], list[FixResult]] = edits_results
+    timeout: float = FIX_TIMEOUT  # LLM 대기 시간(초)
 
     def rank(self, label: str) -> int:
         """중요한 대상일수록 작은 값. choices에 없는 배지 글자는 맨 뒤다."""
diff --git a/src/llm_lab/kinds/rte.py b/src/llm_lab/kinds/rte.py
index 6fd9db2..3de68b9 100644
--- a/src/llm_lab/kinds/rte.py
+++ b/src/llm_lab/kinds/rte.py
@@ -7,6 +7,30 @@ from typing import Any
 from llm_lab.kinds.base import Cell, Kind
 from llm_lab.polyspace import Finding, cell_text, line_number, norm
 
+SYSTEM_PROMPT = """\
+You review one Polyspace Code Prover run-time check in C code \
+and decide whether the code must change.
+- Red Check: Polyspace proved that the operation fails whenever it runs. Fix it.
+- Orange Check: Polyspace could not prove the operation safe. \
+Fix it if some execution can really fail. \
+If the code shown guarantees safety, answer no_fix and name the lines that guarantee it.
+When you fix:
+- Make the safety provable by static analysis: explicit range checks, \
+division-by-zero checks, initialization and similar.
+- Change as little as possible. Do not touch lines unrelated to the check. \
+Keep the behavior for valid inputs.
+- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
+without the line-number prefix. "replacement" is the new text for those lines.
+- Keep each edit small: "original" holds only the lines you change, not the whole function. \
+Add one unchanged neighbouring line only when the changed line appears more than once.
+Do not guess definitions (macros, types, other functions) that are not shown. \
+If they matter, say so in reason.
+Write "reason" in Korean, one to three sentences that a reviewer can paste \
+into a Polyspace comment.
+Answer with JSON only. "findings" has one entry for each numbered finding. \
+"edits" is [] when no finding is fix.
+"""
+
 # TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
 TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}
 
@@ -47,4 +71,8 @@ RTE = Kind(
     choices=("Red", "Orange"),
     defaults=("Red", "Orange"),
     colors={"Red": "red", "Orange": "orange"},
+    system_prompt=SYSTEM_PROMPT,
+    heading="Polyspace Code Prover result",
+    # 4: 공통 답 형식 (findings + edits)
+    prompt_version=4,
 )
diff --git a/src/llm_lab/store.py b/src/llm_lab/store.py
index 8dda78c..2d0f070 100644
--- a/src/llm_lab/store.py
+++ b/src/llm_lab/store.py
@@ -277,13 +277,16 @@ class Store:
             found = db.execute("SELECT id FROM jobs WHERE state = 'running' ORDER BY id")
             return [row["id"] for row in found.fetchall()]
 
-    def cache_get(self, key: str) -> FixResult | None:
+    def cache_get(self, key: str) -> list[FixResult] | None:
+        """묶음의 행별 결과 (행 순서대로). 묶음 처리 전의 예전 값(결과 하나)은 없는 것으로 본다."""
         with self._db() as db:
             found = db.execute("SELECT result FROM cache WHERE key = ?", (key,)).fetchone()
-        return FixResult.from_json(found["result"]) if found else None
+        data = json.loads(found["result"]) if found else None
+        if not isinstance(data, list):
+            return None
+        return [FixResult.from_dict(item) for item in data]
 
-    def cache_put(self, key: str, result: FixResult) -> None:
+    def cache_put(self, key: str, results: list[FixResult]) -> None:
+        text = json.dumps([asdict(result) for result in results], ensure_ascii=False)
         with self._write() as db:
-            db.execute(
-                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, result.to_json(), _now())
-            )
+            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, text, _now()))
diff --git a/src/llm_lab/worker.py b/src/llm_lab/worker.py
index 4f3bc9c..76ab17a 100644
--- a/src/llm_lab/worker.py
+++ b/src/llm_lab/worker.py
@@ -10,7 +10,15 @@ from pathlib import Path
 
 from openai import OpenAI
 
-from llm_lab.fixer import Exchange, FatalLLMError, cache_key, propose_fix, user_error
+from llm_lab.fixer import (
+    Exchange,
+    FatalLLMError,
+    cache_key,
+    make_request,
+    propose_fixes,
+    user_error,
+)
+from llm_lab.kinds import RTE
 from llm_lab.pages import DECISIONS
 from llm_lab.polyspace import Finding
 from llm_lab.store import Store
@@ -88,15 +96,18 @@ class Worker:
         row = self.store.row(row_id)
         assert row.file_name is not None
         source = self.store.source(job_id, row.file_name)
-        key = cache_key(self.model, source, row.finding)
         exchanges: list[Exchange] = []
         reused = False
         try:
-            result = self.store.cache_get(key)
-            reused = result is not None
-            if result is None:
-                result = propose_fix(self.client, self.model, row.finding, source, exchanges)
-                self.store.cache_put(key, result)
+            # 작업에 종류를 저장하기 전이라 모든 작업을 RTE로 처리한다
+            request = make_request(RTE, [row.finding], source)
+            key = cache_key(self.model, request)
+            results = self.store.cache_get(key)
+            reused = results is not None
+            if results is None:
+                results = propose_fixes(self.client, self.model, request, exchanges)
+                self.store.cache_put(key, results)
+            (result,) = results
         except FatalLLMError as exc:
             # 나머지 행도 같은 이유로 실패하므로 작업을 멈춘다. 이 행은 다시 시도 때 처리한다.
             self._save_log(job_id, row.finding, source.name, exchanges, f"작업 중단: {exc}")
````

- [ ] **Step 4: 통과와 린트 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `326 passed`, 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/fixer.py src/llm_lab/kinds src/llm_lab/store.py src/llm_lab/worker.py tests/rte_fixtures.py tests/test_fixer.py tests/test_store.py
```

```bash
git commit -m "feat: ask the LLM about a group of findings in one common answer format" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: DB 스키마 3, 묶음 처리, 멈춤, 묶음 기록 (`store.py`, `worker.py`, `transcript.py`)

**Files:**
- Modify: `src/llm_lab/store.py` (스키마 3: `jobs.kind`·`note`·`context`, `rows.grp`, `ADDED_COLUMNS`로 옮기기, `claim_row` → `claim_rows`, `stop`, `USER_STOP`)
- Modify: `src/llm_lab/worker.py` (묶음으로 모아 중요한 대상부터 제출, 묶음 하나에 LLM 한 번, 작업 정보 JSON을 요청에 넘김)
- Modify: `src/llm_lab/transcript.py` (묶음 기록: 머리, 행마다 한 줄, 결과 여러 줄, 파일 이름은 묶음의 가장 작은 엑셀 행)
- Test: `tests/test_store.py`, `tests/test_worker.py`, `tests/test_transcript.py`

**Interfaces:**
- Consumes: `make_request`, `cache_key`, `propose_fixes` (Task 2), `KINDS` (Task 1)
- Produces:
  - `store.SCHEMA_VERSION = 3`, `store.ADDED_COLUMNS`, `store.USER_STOP = "사용자가 멈췄습니다."`
  - `Job.kind: str = "rte"`, `Job.note: str = ""`, `Job.context: str = ""`
  - `Row.grp: str = ""`, `NewRow.grp: str = ""`
  - `Store.create_job(excel_name, sheet, skipped, files, rows, source_root="", kind="rte", note="", context="") -> int`
  - `Store.claim_rows(row_ids: list[int]) -> bool` (전부 아니면 하나도 안 바꿈), `Store.stop(job_id) -> None`
  - `transcript.render_log(*, job_id, kind, findings, source_name, model, endpoint, extra_body, exchanges, outcomes) -> str`
  - `transcript.write_log(folder, job_id, row: int, text) -> Path`

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_store.py b/tests/test_store.py
index c916222..26af850 100644
--- a/tests/test_store.py
+++ b/tests/test_store.py
@@ -6,7 +6,7 @@ import pytest
 from llm_lab.fixer import Edit, FixResult
 from llm_lab.polyspace import Finding
 from llm_lab.sources import decode_source
-from llm_lab.store import SCHEMA_VERSION, NewRow, Store, StoreError
+from llm_lab.store import SCHEMA_VERSION, USER_STOP, NewRow, Store, StoreError
 
 SOURCE = decode_source("calc.c", "/* 합계 */\r\nint x;\r\n".encode("cp949"))
 FINDING = Finding(
@@ -119,13 +119,47 @@ def test_jobs_lists_newest_first(tmp_path):
     assert [job.id for job in store.jobs(limit=2)] == [ids[2], ids[1]]
 
 
-def test_claim_row_succeeds_only_once(tmp_path):
+def test_claim_rows_takes_the_whole_group_or_nothing(tmp_path):
     store = Store(tmp_path / "fixer.db")
-    row_id = store.rows(make_job(store, "pending"))[0].id
+    first, second = (row.id for row in store.rows(make_job(store, "pending", "pending")))
 
-    assert store.claim_row(row_id) is True
-    assert store.claim_row(row_id) is False
-    assert store.row(row_id).state == "running"
+    assert store.claim_rows([first, second]) is True
+    assert store.claim_rows([first, second]) is False
+    assert [store.row(i).state for i in (first, second)] == ["running", "running"]
+
+    store.set_row(second, "pending")
+    assert store.claim_rows([first, second]) is False  # 하나라도 pending이 아니면 그대로 둔다
+    assert store.row(second).state == "pending"
+
+
+def test_job_keeps_kind_note_and_context_and_rows_keep_their_group(tmp_path):
+    store = Store(tmp_path / "fixer.db")
+    rows = [
+        NewRow(FINDING, "calc.c", "pending", grp="calc.c:2"),
+        NewRow(FINDING, "calc.c", "pending"),
+    ]
+
+    job_id = store.create_job(
+        "m.xlsx", "MISRA_Result", 0, [SOURCE], rows, kind="misra", note="메모", context='{"a": 1}'
+    )
+
+    job = store.job(job_id)
+    assert (job.kind, job.note, job.context) == ("misra", "메모", '{"a": 1}')
+    assert [row.grp for row in store.rows(job_id)] == ["calc.c:2", ""]
+    old_style = store.job(make_job(store, "pending"))
+    assert (old_style.kind, old_style.note, old_style.context) == ("rte", "", "")
+
+
+def test_stop_only_stops_running_jobs(tmp_path):
+    store = Store(tmp_path / "fixer.db")
+    running = make_job(store, "pending")
+    done = make_job(store, "no_source")
+
+    store.stop(running)
+    store.stop(done)
+
+    assert (store.job(running).state, store.job(running).message) == ("stopped", USER_STOP)
+    assert store.job(done).state == "done"
 
 
 def user_version(path):
@@ -185,5 +219,23 @@ def test_database_from_schema_1_gets_source_folder_column(tmp_path):
 
     store = Store(path)
 
-    assert user_version(path) == SCHEMA_VERSION == 2
+    assert user_version(path) == SCHEMA_VERSION == 3
     assert store.job(job_id).source_root == ""
+
+
+def test_database_from_schema_2_gets_kind_note_context_and_group_columns(tmp_path):
+    path = tmp_path / "fixer.db"
+    job_id = make_job(Store(path), "pending")
+    with closing(sqlite3.connect(path)) as db:  # 스키마 2에는 이 칸들이 없었다
+        for table, column in [("jobs", "kind"), ("jobs", "note"), ("jobs", "context")]:
+            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
+        db.execute("ALTER TABLE rows DROP COLUMN grp")
+        db.execute("PRAGMA user_version = 2")
+        db.commit()
+
+    store = Store(path)
+
+    assert user_version(path) == SCHEMA_VERSION
+    job = store.job(job_id)
+    assert (job.kind, job.note, job.context) == ("rte", "", "")
+    assert store.rows(job_id)[0].grp == ""
diff --git a/tests/test_transcript.py b/tests/test_transcript.py
index e2c412b..00ebe06 100644
--- a/tests/test_transcript.py
+++ b/tests/test_transcript.py
@@ -17,24 +17,30 @@ EXCHANGE = Exchange(
 )
 
 
-def render(exchanges, outcome="수정 - b가 0일 때를 검사합니다.", extra_body=None):
+def render(
+    exchanges, outcomes=("수정 - b가 0일 때를 검사합니다.",), extra_body=None, findings=None
+):
     return render_log(
         job_id=3,
-        finding=finding(),
+        kind="RTE",
+        findings=findings or [finding()],
         source_name="calc.c",
         model="m1",
         endpoint="https://llm.test/v1/chat/completions",
         extra_body=extra_body or {},
         exchanges=exchanges,
-        outcome=outcome,
+        outcomes=list(outcomes),
     )
 
 
 def test_log_shows_row_settings_and_outcome():
     text = render([EXCHANGE], extra_body={"chat_template_kwargs": {"enable_thinking": False}})
 
-    assert text.startswith("# 작업 3 · 엑셀 2행 · Orange Check\n")
-    assert "- 위치: calc.c:3 (divide)" in text
+    assert text.startswith("# 작업 3 · RTE · 엑셀 2행\n")
+    assert (
+        "- 엑셀 2행: Division by zero / Warning: scalar division by zero may occur"
+        " / calc.c:3 (divide)"
+    ) in text
     assert "- 모델: m1" in text
     assert "- 보낸 주소: https://llm.test/v1/chat/completions" in text
     assert '- 함께 보낸 값: {"chat_template_kwargs": {"enable_thinking": false}}' in text
@@ -54,17 +60,28 @@ def test_log_shows_sent_messages_and_reply_verbatim():
     assert '"id": "cmpl-1"' in text  # 응답 원문 JSON
 
 
+def test_log_for_a_group_lists_each_row_and_outcome():
+    findings = [finding(), finding(row=4, line=None, function="", check="Overflow")]
+    outcomes = ["엑셀 2행 수정 - 고침", "엑셀 4행 수정 불필요 - 그대로"]
+
+    text = render([EXCHANGE], outcomes=outcomes, findings=findings)
+
+    assert text.startswith("# 작업 3 · RTE · 엑셀 2·4행\n")
+    assert "- 엑셀 4행: Overflow / Warning: scalar division by zero may occur / calc.c:?\n" in text
+    assert "- 결과: 엑셀 2행 수정 - 고침\n- 결과: 엑셀 4행 수정 불필요 - 그대로\n" in text
+
+
 def test_log_records_errors_and_calls_that_never_happened():
     failed = Exchange(messages=EXCHANGE.messages, seconds=0.2, error="[인증] 인증 실패(401)")
 
-    assert "### 오류\n\n[인증] 인증 실패(401)" in render([failed], outcome="작업 중단")
-    reused = render([], outcome="이전 결과 재사용 - LLM을 부르지 않음")
+    assert "### 오류\n\n[인증] 인증 실패(401)" in render([failed], outcomes=["작업 중단"])
+    reused = render([], outcomes=["이전 결과 재사용 - LLM을 부르지 않음"])
     assert "LLM에 보낸 요청이 없습니다." in reused
 
 
 def test_write_log_never_overwrites(tmp_path):
-    first = write_log(tmp_path / "logs", 3, finding(), "a")
-    second = write_log(tmp_path / "logs", 3, finding(), "b")
+    first = write_log(tmp_path / "logs", 3, 2, "a")
+    second = write_log(tmp_path / "logs", 3, 2, "b")
 
     assert first != second
     assert first.read_text(encoding="utf-8") == "a"
diff --git a/tests/test_worker.py b/tests/test_worker.py
index be502be..9900a2a 100644
--- a/tests/test_worker.py
+++ b/tests/test_worker.py
@@ -3,7 +3,7 @@ import sys
 import threading
 
 from fake_llm import API_KEY, MODEL, error, make_client, scripted
-from rte_fixtures import CALC_C, finding, fix_answer
+from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer
 
 from llm_lab.sources import decode_source
 from llm_lab.store import NewRow, Store
@@ -120,7 +120,7 @@ def test_writes_conversation_log_for_each_row(tmp_path):
     worker.start(job_id)
 
     (text,) = logs(tmp_path)
-    assert f"# 작업 {job_id} · 엑셀 2행 · Orange Check" in text
+    assert f"# 작업 {job_id} · RTE · 엑셀 2행" in text
     assert "- 보낸 주소: https://llm.test/v1/chat/completions" in text
     assert "You review one Polyspace Code Prover" in text  # 시스템 메시지 그대로
     assert "    3|     return a / b;" in text  # 보낸 코드 그대로
@@ -159,6 +159,140 @@ def test_log_records_row_error_and_fatal_stop(tmp_path):
     assert "- 결과: 작업 중단: [인증]" in stop_log
 
 
+def test_group_goes_out_in_one_request_and_each_row_gets_its_decision(tmp_path):
+    # 묶음 안의 순서는 (줄, check, detail, 엑셀 행)이므로 Division by zero(엑셀 5행)가 1번이다
+    handler, sent = scripted(
+        answer(("fix", REASON), ("no_fix", "범위가 보장됨"), edits=[DIVIDE_FIX])
+    )
+    rows = (
+        NewRow(finding(row=2, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
+        NewRow(finding(row=5), "calc.c", "pending", grp="calc.c:3"),
+    )
+    store, worker, job_id = setup(tmp_path, handler, *rows)
+
+    worker.start(job_id)
+
+    assert len(sent) == 1
+    prompt = sent[0]["messages"][1]["content"]
+    assert "Finding 1:\n- TYPE: Orange Check\n- check: Division by zero" in prompt
+    overflow, division = store.rows(job_id)
+    assert (overflow.result.decision, overflow.result.reason) == ("no_fix", "범위가 보장됨")
+    assert (division.result.decision, division.result.reason) == ("fix", REASON)
+    assert store.job(job_id).state == "done"
+    (path,) = (tmp_path / "logs").glob("*.md")
+    assert path.name.endswith(f"_job{job_id}_row2.md")  # 묶음의 가장 작은 엑셀 행
+    text = path.read_text(encoding="utf-8")
+    assert f"# 작업 {job_id} · RTE · 엑셀 5·2행" in text
+    assert "- 결과: 엑셀 5행 수정 - b가 0일 때를 검사합니다.\n" in text
+    assert "- 결과: 엑셀 2행 수정 불필요 - 범위가 보장됨\n" in text
+
+
+def test_group_result_is_reused_when_only_excel_rows_change(tmp_path):
+    handler, sent = scripted(
+        answer(("fix", REASON), ("no_fix", "범위가 보장됨"), edits=[DIVIDE_FIX])
+    )
+    first_rows = (
+        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
+        NewRow(finding(row=5, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
+    )
+    store, worker, first = setup(tmp_path, handler, *first_rows)
+    worker.start(first)
+    # 엑셀을 다시 뽑아 행 번호와 행 순서만 바뀌었다
+    rows = [
+        NewRow(finding(row=12, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
+        NewRow(finding(row=8), "calc.c", "pending", grp="calc.c:3"),
+    ]
+    second = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)
+
+    worker.start(second)
+
+    assert len(sent) == 1
+    overflow, division = store.rows(second)
+    assert (overflow.result.decision, division.result.decision) == ("no_fix", "fix")
+
+
+def test_same_rule_twice_on_one_line_gets_an_answer_each(tmp_path):
+    handler, sent = scripted(answer(("fix", REASON), ("fix", REASON), edits=[DIVIDE_FIX]))
+    rows = (
+        NewRow(finding(row=4), "calc.c", "pending", grp="calc.c:3"),
+        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
+    )
+    store, worker, job_id = setup(tmp_path, handler, *rows)
+
+    worker.start(job_id)
+
+    assert len(sent) == 1
+    assert "Finding 2:" in sent[0]["messages"][1]["content"]
+    assert [row.result.decision for row in store.rows(job_id)] == ["fix", "fix"]
+
+
+def test_groups_are_queued_most_important_first(tmp_path):
+    queued = []
+    handler, sent = scripted(fix_answer(), fix_answer())
+    store = Store(tmp_path / "fixer.db")
+    worker = Worker(store, make_client(handler), MODEL, queued.append)
+    red = finding(row=3, color="Red", type="Red Check", check="Overflow")
+    rows = [NewRow(finding(row=2), "calc.c", "pending"), NewRow(red, "calc.c", "pending")]
+    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)
+
+    worker.start(job_id)
+    for task in queued:
+        task()
+
+    # 엑셀에서는 Orange가 먼저지만 Red를 먼저 보낸다
+    assert ["- TYPE: Red Check" in s["messages"][1]["content"] for s in sent] == [True, False]
+
+
+def test_stopped_job_leaves_queued_groups_until_it_continues(tmp_path):
+    queued = []
+    handler, sent = scripted(fix_answer(), fix_answer())
+    store = Store(tmp_path / "fixer.db")
+    worker = Worker(store, make_client(handler), MODEL, queued.append)
+    rows = [NewRow(finding(), "calc.c", "pending"), NewRow(finding(line=2), "calc.c", "pending")]
+    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)
+    worker.start(job_id)
+
+    store.stop(job_id)
+    for task in queued:
+        task()
+
+    assert sent == []
+    assert [r.state for r in store.rows(job_id)] == ["pending", "pending"]
+    assert store.job(job_id).state == "stopped"
+
+    queued.clear()
+    store.retry(job_id)  # "이어서 처리"
+    worker.start(job_id)
+    for task in queued:
+        task()
+
+    assert [r.state for r in store.rows(job_id)] == ["done", "done"]
+    assert store.job(job_id).state == "done"
+
+
+def test_group_error_marks_every_row_and_fatal_error_puts_them_back(tmp_path):
+    rows = (
+        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
+        NewRow(finding(row=3, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
+    )
+    handler, _ = scripted(error(400, "bad request"))
+    store, worker, job_id = setup(tmp_path / "a", handler, *rows)
+
+    worker.start(job_id)
+
+    states = [(r.state, r.error) for r in store.rows(job_id)]
+    assert [state for state, _ in states] == ["error", "error"]
+    assert states[0][1] == states[1][1] != ""
+
+    handler, _ = scripted(error(401, "invalid key"))
+    store, worker, job_id = setup(tmp_path / "b", handler, *rows)
+
+    worker.start(job_id)
+
+    assert [r.state for r in store.rows(job_id)] == ["pending", "pending"]
+    assert store.job(job_id).state == "stopped"
+
+
 def test_daemon_pool_runs_tasks():
     done = threading.Event()
 
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q`
Expected: `ERROR tests/test_store.py` — `ImportError: cannot import name 'USER_STOP' from 'llm_lab.store'`

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/store.py b/src/llm_lab/store.py
index 2d0f070..1101e8d 100644
--- a/src/llm_lab/store.py
+++ b/src/llm_lab/store.py
@@ -17,7 +17,8 @@ from llm_lab.sources import SourceFile
 
 # 표 구조를 바꾸면 올리고 옛 DB를 옮기는 코드를 넣는다. PRAGMA user_version에 적는다.
 # 2: jobs.source_root (소스를 읽은 폴더. 파일을 하나씩 올리던 예전 작업은 '')
-SCHEMA_VERSION = 2
+# 3: jobs.kind·note·context (시트 종류, 작업 메모, 작업 정보 JSON), rows.grp (묶음)
+SCHEMA_VERSION = 3
 
 SCHEMA = """
 CREATE TABLE IF NOT EXISTS jobs (
@@ -28,7 +29,10 @@ CREATE TABLE IF NOT EXISTS jobs (
     skipped INTEGER NOT NULL,
     state TEXT NOT NULL,
     message TEXT NOT NULL DEFAULT '',
-    source_root TEXT NOT NULL DEFAULT ''
+    source_root TEXT NOT NULL DEFAULT '',
+    kind TEXT NOT NULL DEFAULT 'rte',
+    note TEXT NOT NULL DEFAULT '',
+    context TEXT NOT NULL DEFAULT ''
 );
 CREATE TABLE IF NOT EXISTS files (
     job_id INTEGER NOT NULL,
@@ -47,7 +51,8 @@ CREATE TABLE IF NOT EXISTS rows (
     file_name TEXT,
     state TEXT NOT NULL,
     result TEXT,
-    error TEXT NOT NULL DEFAULT ''
+    error TEXT NOT NULL DEFAULT '',
+    grp TEXT NOT NULL DEFAULT ''
 );
 CREATE TABLE IF NOT EXISTS cache (
     key TEXT PRIMARY KEY,
@@ -56,6 +61,17 @@ CREATE TABLE IF NOT EXISTS cache (
 );
 """
 
+# 예전 DB에 없을 수 있는 칸 (표, 칸, 정의). 켤 때 없으면 기본값으로 더한다.
+ADDED_COLUMNS = [
+    ("jobs", "source_root", "TEXT NOT NULL DEFAULT ''"),
+    ("jobs", "kind", "TEXT NOT NULL DEFAULT 'rte'"),
+    ("jobs", "note", "TEXT NOT NULL DEFAULT ''"),
+    ("jobs", "context", "TEXT NOT NULL DEFAULT ''"),
+    ("rows", "grp", "TEXT NOT NULL DEFAULT ''"),
+]
+# 사용자가 "멈춤"을 눌러 멈춘 작업의 메시지. 화면은 치명 오류로 멈춘 작업과 다르게 보인다.
+USER_STOP = "사용자가 멈췄습니다."
+
 
 class StoreError(Exception):
     """DB를 쓸 수 없음."""
@@ -71,6 +87,9 @@ class Job:
     state: str  # running / done / stopped
     message: str
     source_root: str = ""  # 소스를 읽은 폴더. 파일을 하나씩 올리던 예전 작업은 ''
+    kind: str = "rte"  # 시트 종류 (llm_lab.kinds). 스키마 3 전의 작업은 모두 RTE
+    note: str = ""  # 작업 메모 한 줄 (조건 시트 요약, 뺀 행의 이유별 개수)
+    context: str = ""  # 종류가 엑셀 전체에서 읽은 작업 정보 (JSON, 없으면 '')
 
 
 @dataclass(frozen=True)
@@ -82,6 +101,7 @@ class Row:
     state: str  # pending / running / done / error / no_source
     result: FixResult | None
     error: str
+    grp: str = ""  # 묶음 키. 키가 같은 행은 LLM에 함께 보낸다 ('' = 혼자)
 
 
 @dataclass(frozen=True)
@@ -90,6 +110,7 @@ class NewRow:
     file_name: str | None
     state: str
     error: str = ""
+    grp: str = ""
 
 
 def _now() -> str:
@@ -111,10 +132,11 @@ class Store:
                 )
             db.execute("PRAGMA journal_mode=WAL")
             db.executescript(SCHEMA)
-            # 버전 0·1: 새 DB이거나 jobs.source_root가 생기기 전에 만든 DB
-            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
-            if "source_root" not in columns:
-                db.execute("ALTER TABLE jobs ADD COLUMN source_root TEXT NOT NULL DEFAULT ''")
+            # 예전 버전 DB: 그 뒤에 생긴 칸을 더한다
+            for table, column, definition in ADDED_COLUMNS:
+                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
+                if column not in columns:
+                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
             db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
 
     @contextmanager
@@ -140,13 +162,16 @@ class Store:
         files: list[SourceFile],
         rows: list[NewRow],
         source_root: str = "",
+        kind: str = "rte",
+        note: str = "",
+        context: str = "",
     ) -> int:
         state = "running" if any(row.state == "pending" for row in rows) else "done"
         with self._write() as db:
             cursor = db.execute(
-                "INSERT INTO jobs (created_at, excel_name, sheet, skipped, state, source_root) "
-                "VALUES (?, ?, ?, ?, ?, ?)",
-                (_now(), excel_name, sheet, skipped, state, source_root),
+                "INSERT INTO jobs (created_at, excel_name, sheet, skipped, state, source_root, "
+                "kind, note, context) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
+                (_now(), excel_name, sheet, skipped, state, source_root, kind, note, context),
             )
             job_id = cursor.lastrowid
             assert job_id is not None
@@ -158,8 +183,8 @@ class Store:
                 ],
             )
             db.executemany(
-                "INSERT INTO rows (job_id, finding, file_name, state, error) "
-                "VALUES (?, ?, ?, ?, ?)",
+                "INSERT INTO rows (job_id, finding, file_name, state, error, grp) "
+                "VALUES (?, ?, ?, ?, ?, ?)",
                 [
                     (
                         job_id,
@@ -167,6 +192,7 @@ class Store:
                         r.file_name,
                         r.state,
                         r.error,
+                        r.grp,
                     )
                     for r in rows
                 ],
@@ -212,6 +238,7 @@ class Store:
             state=record["state"],
             result=FixResult.from_json(record["result"]) if record["result"] else None,
             error=record["error"],
+            grp=record["grp"],
         )
 
     def source(self, job_id: int, name: str) -> SourceFile:
@@ -237,13 +264,20 @@ class Store:
                 (state, result.to_json() if result else None, error, row_id),
             )
 
-    def claim_row(self, row_id: int) -> bool:
-        """pending 행을 running으로 바꾼다. 다른 작업자가 먼저 가져갔으면 False."""
+    def claim_rows(self, row_ids: list[int]) -> bool:
+        """묶음의 행이 모두 pending이면 한꺼번에 running으로 바꾼다.
+
+        하나라도 pending이 아니면(다른 작업자가 먼저 가져갔으면) 아무것도 바꾸지 않고 False.
+        """
+        marks = ", ".join("?" * len(row_ids))
         with self._write() as db:
-            cursor = db.execute(
-                "UPDATE rows SET state = 'running' WHERE id = ? AND state = 'pending'", (row_id,)
-            )
-            return cursor.rowcount == 1
+            pending = db.execute(
+                f"SELECT COUNT(*) FROM rows WHERE id IN ({marks}) AND state = 'pending'", row_ids
+            ).fetchone()[0]
+            if pending != len(row_ids):
+                return False
+            db.execute(f"UPDATE rows SET state = 'running' WHERE id IN ({marks})", row_ids)
+            return True
 
     def set_job(self, job_id: int, state: str, message: str = "") -> None:
         with self._write() as db:
@@ -251,6 +285,14 @@ class Store:
                 "UPDATE jobs SET state = ?, message = ? WHERE id = ?", (state, message, job_id)
             )
 
+    def stop(self, job_id: int) -> None:
+        """처리 중인 작업을 멈춘다. 줄 서 있던 묶음은 건너뛰고, "이어서 처리"(retry)로 계속한다."""
+        with self._write() as db:
+            db.execute(
+                "UPDATE jobs SET state = 'stopped', message = ? WHERE id = ? AND state = 'running'",
+                (USER_STOP, job_id),
+            )
+
     def finish_if_done(self, job_id: int) -> None:
         """남은 pending·running 행이 없으면 running 작업을 done으로 바꾼다."""
         with self._write() as db:
diff --git a/src/llm_lab/transcript.py b/src/llm_lab/transcript.py
index ee2c16c..c7d1b78 100644
--- a/src/llm_lab/transcript.py
+++ b/src/llm_lab/transcript.py
@@ -23,31 +23,34 @@ def _fence(text: str, lang: str = "text") -> str:
 def render_log(
     *,
     job_id: int,
-    finding: Finding,
+    kind: str,
+    findings: list[Finding],
     source_name: str,
     model: str,
     endpoint: str,
     extra_body: Mapping[str, Any],
     exchanges: list[Exchange],
-    outcome: str,
+    outcomes: list[str],
 ) -> str:
-    """행 하나의 처리 기록: 행 정보, 보낸 메시지와 받은 응답을 그대로 적는다."""
-    where = f"{source_name}:{finding.line if finding.line is not None else '?'}"
-    if finding.function:
-        where += f" ({finding.function})"
+    """묶음 하나의 처리 기록: 행 정보, 보낸 메시지와 받은 응답을 그대로 적는다.
+
+    kind는 종류의 화면 이름, findings는 LLM 메시지의 Finding 1, 2 … 순서다.
+    """
+    rows = "·".join(str(finding.row) for finding in findings)
     lines = [
-        f"# 작업 {job_id} · 엑셀 {finding.row}행 · {finding.type}",
+        f"# 작업 {job_id} · {kind} · 엑셀 {rows}행",
         "",
         f"- 기록 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
-        f"- check: {finding.check}",
-        f"- detail: {finding.detail}",
-        f"- 위치: {where}",
-        f"- 모델: {model}",
-        f"- 보낸 주소: {endpoint}",
     ]
+    for finding in findings:
+        where = f"{source_name}:{finding.line if finding.line is not None else '?'}"
+        if finding.function:
+            where += f" ({finding.function})"
+        lines.append(f"- 엑셀 {finding.row}행: {finding.check} / {finding.detail} / {where}")
+    lines += [f"- 모델: {model}", f"- 보낸 주소: {endpoint}"]
     if extra_body:
         lines.append(f"- 함께 보낸 값: {json.dumps(extra_body, ensure_ascii=False)}")
-    lines.append(f"- 결과: {outcome}")
+    lines += [f"- 결과: {outcome}" for outcome in outcomes]
     if not exchanges:
         lines += ["", "LLM에 보낸 요청이 없습니다."]
     for number, exchange in enumerate(exchanges, start=1):
@@ -77,10 +80,13 @@ def render_log(
     return "\n".join(lines) + "\n"
 
 
-def write_log(folder: Path, job_id: int, finding: Finding, text: str) -> Path:
-    """시각_job작업_row엑셀행.md로 저장한다. 같은 이름이 있으면 -2, -3을 붙여 덮어쓰지 않는다."""
+def write_log(folder: Path, job_id: int, row: int, text: str) -> Path:
+    """시각_job작업_row엑셀행.md로 저장한다. row는 묶음의 가장 작은 엑셀 행이다.
+
+    같은 이름이 있으면 -2, -3을 붙여 덮어쓰지 않는다.
+    """
     folder.mkdir(parents=True, exist_ok=True)
-    stem = f"{datetime.now():%Y%m%d-%H%M%S}_job{job_id}_row{finding.row}"
+    stem = f"{datetime.now():%Y%m%d-%H%M%S}_job{job_id}_row{row}"
     path = folder / f"{stem}.md"
     number = 2
     while path.exists():
diff --git a/src/llm_lab/worker.py b/src/llm_lab/worker.py
index 76ab17a..7f63a5a 100644
--- a/src/llm_lab/worker.py
+++ b/src/llm_lab/worker.py
@@ -1,7 +1,8 @@
-"""작업의 RTE 행을 뒤에서 하나씩 LLM에 보내 처리한다."""
+"""작업의 행을 묶음으로 모아, 뒤에서 묶음마다 LLM에 보내 처리한다."""
 
 from __future__ import annotations
 
+import json
 import logging
 import queue
 import threading
@@ -18,10 +19,10 @@ from llm_lab.fixer import (
     propose_fixes,
     user_error,
 )
-from llm_lab.kinds import RTE
+from llm_lab.kinds import KINDS, Kind
 from llm_lab.pages import DECISIONS
 from llm_lab.polyspace import Finding
-from llm_lab.store import Store
+from llm_lab.store import Row, Store
 from llm_lab.transcript import render_log, write_log
 
 Submit = Callable[[Callable[[], None]], object]
@@ -65,10 +66,23 @@ class Worker:
         self.log_dir = log_dir
 
     def start(self, job_id: int) -> None:
-        """작업의 pending 행을 모두 제출한다."""
+        """작업의 pending 행을 묶음으로 모아, 중요한 대상부터 묶음마다 하나씩 제출한다."""
+        job = self.store.job(job_id)
+        if job is None:
+            return
+        kind = KINDS[job.kind]
+        groups: dict[tuple[str, int], list[Row]] = {}
         for row in self.store.rows(job_id):
             if row.state == "pending":
-                self._submit(lambda row_id=row.id: self._run(job_id, row_id))
+                # grp가 같은 행은 한 묶음, grp가 없는 행은 혼자 묶음이다
+                groups.setdefault((row.grp, 0) if row.grp else ("", row.id), []).append(row)
+
+        def order(rows: list[Row]) -> tuple[int, int]:
+            return min(kind.priority(r.finding) for r in rows), min(r.finding.row for r in rows)
+
+        for rows in sorted(groups.values(), key=order):
+            row_ids = [row.id for row in rows]
+            self._submit(lambda row_ids=row_ids: self._run(job_id, row_ids))
         self.store.finish_if_done(job_id)
 
     def resume(self) -> None:
@@ -76,81 +90,101 @@ class Worker:
         for job_id in self.store.recover():
             self.start(job_id)
 
-    def _run(self, job_id: int, row_id: int) -> None:
+    def _run(self, job_id: int, row_ids: list[int]) -> None:
         try:
-            self._process(job_id, row_id)
+            self._process(job_id, row_ids)
         except Exception:  # 작업자 스레드의 예외는 아무 데도 보이지 않으므로 남긴다
-            log.exception("행 %s 처리 중 예상하지 못한 오류", row_id)
+            log.exception("행 %s 처리 중 예상하지 못한 오류", row_ids)
             message = user_error(
                 "처리 중 예상하지 못한 문제가 생겼습니다.",
                 "서버를 켠 창에 나온 오류 내용을 알려 주세요.",
             )
-            self.store.set_row(row_id, "error", error=message)
+            for row_id in row_ids:
+                self.store.set_row(row_id, "error", error=message)
             self.store.finish_if_done(job_id)
 
-    def _process(self, job_id: int, row_id: int) -> None:
+    def _process(self, job_id: int, row_ids: list[int]) -> None:
         job = self.store.job(job_id)
-        # 다시 시도를 거듭 누르면 같은 행이 두 번 제출되므로, 먼저 가져간 작업자만 처리한다
-        if job is None or job.state != "running" or not self.store.claim_row(row_id):
-            return  # 작업이 멈췄거나 이미 처리 중이거나 끝난 행
-        row = self.store.row(row_id)
-        assert row.file_name is not None
-        source = self.store.source(job_id, row.file_name)
+        # 다시 시도를 거듭 누르면 같은 묶음이 두 번 제출되므로, 먼저 가져간 작업자만 처리한다
+        if job is None or job.state != "running" or not self.store.claim_rows(row_ids):
+            return  # 작업이 멈췄거나 이미 처리 중이거나 끝난 묶음
+        kind = KINDS[job.kind]
+        rows = sorted((self.store.row(row_id) for row_id in row_ids), key=_order_in_group)
+        findings = [row.finding for row in rows]
+        assert rows[0].file_name is not None
+        source = self.store.source(job_id, rows[0].file_name)
+        context = json.loads(job.context) if job.context else None
         exchanges: list[Exchange] = []
         reused = False
         try:
-            # 작업에 종류를 저장하기 전이라 모든 작업을 RTE로 처리한다
-            request = make_request(RTE, [row.finding], source)
+            request = make_request(kind, findings, source, context)
             key = cache_key(self.model, request)
             results = self.store.cache_get(key)
             reused = results is not None
             if results is None:
                 results = propose_fixes(self.client, self.model, request, exchanges)
                 self.store.cache_put(key, results)
-            (result,) = results
         except FatalLLMError as exc:
-            # 나머지 행도 같은 이유로 실패하므로 작업을 멈춘다. 이 행은 다시 시도 때 처리한다.
-            self._save_log(job_id, row.finding, source.name, exchanges, f"작업 중단: {exc}")
-            self.store.set_row(row_id, "pending")
+            # 나머지 묶음도 같은 이유로 실패하므로 작업을 멈춘다. 이 묶음은 다시 시도 때 처리한다.
+            self._save_log(job_id, kind, findings, source.name, exchanges, [f"작업 중단: {exc}"])
+            for row in rows:
+                self.store.set_row(row.id, "pending")
             self.store.set_job(job_id, "stopped", str(exc))
             return
         except Exception as exc:
             error = str(exc) or type(exc).__name__
             # 기록 파일의 "결과" 줄은 한 줄이어야 목록이 깨지지 않는다
             outcome = "오류: " + " / ".join(error.splitlines())
-            self._save_log(job_id, row.finding, source.name, exchanges, outcome)
-            self.store.set_row(row_id, "error", error=error)
+            self._save_log(job_id, kind, findings, source.name, exchanges, [outcome])
+            for row in rows:
+                self.store.set_row(row.id, "error", error=error)
         else:
             if reused:
-                outcome = "이전 결과 재사용 - LLM을 부르지 않음"
+                outcomes = ["이전 결과 재사용 - LLM을 부르지 않음"]
             else:
-                outcome = f"{DECISIONS[result.decision]} - {result.reason}"
-            self._save_log(job_id, row.finding, source.name, exchanges, outcome)
-            self.store.set_row(row_id, "done", result=result)
+                outcomes = [f"{DECISIONS[result.decision]} - {result.reason}" for result in results]
+                if len(rows) > 1:
+                    outcomes = [
+                        f"엑셀 {row.finding.row}행 {outcome}"
+                        for row, outcome in zip(rows, outcomes, strict=True)
+                    ]
+            self._save_log(job_id, kind, findings, source.name, exchanges, outcomes)
+            for row, result in zip(rows, results, strict=True):
+                self.store.set_row(row.id, "done", result=result)
         self.store.finish_if_done(job_id)
 
     def _save_log(
         self,
         job_id: int,
-        finding: Finding,
+        kind: Kind,
+        findings: list[Finding],
         source_name: str,
         exchanges: list[Exchange],
-        outcome: str,
+        outcomes: list[str],
     ) -> None:
-        """대화 기록 파일을 남긴다. 기록을 못 남겨도 행 처리는 계속한다."""
+        """대화 기록 파일을 남긴다. 기록을 못 남겨도 묶음 처리는 계속한다."""
         if self.log_dir is None:
             return
         text = render_log(
             job_id=job_id,
-            finding=finding,
+            kind=kind.title,
+            findings=findings,
             source_name=source_name,
             model=self.model,
             endpoint=f"{self.client.base_url}chat/completions",
             extra_body=getattr(self.client, "chat_extra_body", {}),
             exchanges=exchanges,
-            outcome=outcome,
+            outcomes=outcomes,
         )
+        first_row = min(finding.row for finding in findings)
         try:
-            write_log(self.log_dir, job_id, finding, text)
+            write_log(self.log_dir, job_id, first_row, text)
         except OSError:
-            log.exception("대화 기록을 남기지 못함: 작업 %s 엑셀 %s행", job_id, finding.row)
+            log.exception("대화 기록을 남기지 못함: 작업 %s 엑셀 %s행", job_id, first_row)
+
+
+def _order_in_group(row: Row) -> tuple[int, str, str, int]:
+    """묶음 안의 순서 = LLM 메시지의 Finding 번호 순서. 엑셀 행은 마지막 기준이라
+    엑셀을 다시 뽑아 행 번호가 바뀌어도 같은 지적이면 순서와 결과 재사용 키가 그대로다."""
+    finding = row.finding
+    return finding.line or 0, finding.check, finding.detail, finding.row
````

- [ ] **Step 4: 통과와 린트 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `336 passed`, 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/store.py src/llm_lab/worker.py src/llm_lab/transcript.py tests/test_store.py tests/test_worker.py tests/test_transcript.py
```

```bash
git commit -m "feat: process findings in groups and let jobs be stopped" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: MISRA 종류와 조건 시트 (`kinds/misra.py`)

**Files:**
- Create: `src/llm_lab/kinds/misra.py` (조건 시트 읽기, 규칙 번호 맞추기, 분류 정하기, 같은 줄 묶음, `facts`, 지시문)
- Modify: `src/llm_lab/kinds/base.py` (`group_key` 필드, 기본 `alone`), `src/llm_lab/kinds/__init__.py` (`KINDS`에 MISRA)
- Test: `tests/misra_fixtures.py`(새 파일, 만들어 낸 값), `tests/test_kinds.py`, `tests/test_polyspace.py`, `tests/test_fixer.py`

**Interfaces:**
- Consumes: `polyspace`의 `Finding`, `Prepared`, `cell_text`, `data_rows`, `find_header`, `line_number`, `norm` (Task 1), `Kind`의 지시문 필드 (Task 2)
- Produces:
  - `kinds.MISRA`, `KINDS["misra"]`
  - `kinds.misra`: `CATEGORIES`, `NO_CATEGORY = "분류 없음"`, `DISABLED = "적용 안 함 (조건 시트)"`, `NO_CONDITIONS = "조건 시트 없음: 결과 시트 분류를 씀"`, `OFF_WORDS`, `rule_key(text, directive=False) -> str | None`, `category_word(text) -> str`, `read_conditions(workbook) -> Prepared`(data는 `{규칙 번호: [분류, 켜짐]}`), `read_row`, `category_of`, `same_line`, `facts`, `SYSTEM_PROMPT`
  - `Kind.group_key(finding, file_name) -> str` (기본 `alone`, `''`)
  - 시험 데이터: `misra_fixtures.MISRA_HEADER`, `CONDITION_ROWS`, `misra_row(...)`, `misra_finding(**overrides)`, `open_workbook(data)`

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/misra_fixtures.py b/tests/misra_fixtures.py
new file mode 100644
index 0000000..e56131e
--- /dev/null
+++ b/tests/misra_fixtures.py
@@ -0,0 +1,71 @@
+"""MISRA 테스트용 데이터. 값은 모두 만들어 낸 것이다 (회사 엑셀의 값을 넣지 않는다)."""
+
+import io
+
+from openpyxl import load_workbook
+from rte_fixtures import CALC_PATH
+
+from llm_lab.polyspace import Finding
+
+MISRA_HEADER = [
+    "TYPE",
+    "Group",
+    "information",
+    "File",
+    "Function",
+    "Line",
+    "Check",
+    "Detail",
+    "Status",
+    "Comment",
+]
+
+# 조건 시트: 이름도 열 배치도 결과 시트와 다르다. 첫 열은 비어 있고, 헤더 끝에 콜론이 붙고,
+# Mode의 대소문자가 섞이고, 꺼진 규칙과 빈 줄과 Mode가 빈 줄이 있다.
+CONDITION_ROWS = [
+    ["규칙 목록"],
+    [None, "Guideline", "Description", "MODE", "Enabled:"],
+    [None, "10.4", "operands", "required", "yes"],
+    [None, "Rule 12.1", "precedence", "ADVISORY", "Yes"],
+    [None, "2.2", "dead code", "advisory", "no"],
+    [None, "Dir 4.6", "basic types", "Mandatory", None],
+    [None, "9.1", "init", "disapplied", "YES"],
+    [None, None, None, None, None],
+    [None, "17.7", "return value", None, "yes"],
+]
+
+
+def misra_row(
+    check: str = "10.4 Operands shall share an essential type category",
+    *,
+    category: str = "Required",
+    line: object = 3,
+    file: str = CALC_PATH,
+    group: str = "10 The essential type model",
+) -> list:
+    information = f"Category: {category}" if category else ""
+    detail = "Operands have different essential types"
+    return ["MISRA C:2012", group, information, file, "divide", line, check, detail, "", ""]
+
+
+def misra_finding(**overrides: object) -> Finding:
+    """misra_row() 기본값과 같은 행."""
+    values: dict = {
+        "row": 2,
+        "color": "",
+        "type": "MISRA C:2012",
+        "file": CALC_PATH,
+        "line": 3,
+        "check": "10.4 Operands shall share an essential type category",
+        "detail": "Operands have different essential types",
+        "group": "10 The essential type model",
+        "information": "Category: Required",
+        "function": "divide",
+        "category": "Required",
+    }
+    return Finding(**{**values, **overrides})
+
+
+def open_workbook(data: bytes):
+    """read_sheet가 여는 것과 같은 방식(읽기 전용)으로 연 엑셀."""
+    return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
diff --git a/tests/test_fixer.py b/tests/test_fixer.py
index a9a15d5..496e72b 100644
--- a/tests/test_fixer.py
+++ b/tests/test_fixer.py
@@ -3,6 +3,7 @@ from dataclasses import replace
 
 import pytest
 from fake_llm import MODEL, completion, error, make_client, scripted
+from misra_fixtures import misra_finding
 from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer
 
 from llm_lab.fixer import (
@@ -15,7 +16,7 @@ from llm_lab.fixer import (
     make_request,
     propose_fixes,
 )
-from llm_lab.kinds import RTE
+from llm_lab.kinds import MISRA, RTE
 from llm_lab.sources import decode_source
 
 SOURCE = decode_source("calc.c", CALC_C)
@@ -195,6 +196,24 @@ def test_rte_prompt_asks_for_small_edits_and_the_common_answer():
     assert '"findings" has one entry for each numbered finding' in RTE.system_prompt
 
 
+def test_misra_request_sends_the_category_and_the_same_line_rules():
+    findings = [misra_finding(category="Mandatory"), misra_finding(row=4, check="12.1 Precedence")]
+
+    request = make_request(MISRA, findings, SOURCE)
+
+    prompt = request.messages[1]["content"]
+    assert prompt.startswith(
+        "Polyspace MISRA C:2012 results:\n\nFinding 1:\n- TYPE: MISRA C:2012\n"
+    )
+    assert "- category: Mandatory\n" in prompt
+    assert "Finding 2:\n- TYPE: MISRA C:2012\n- check: 12.1 Precedence\n" in prompt
+    assert "information" not in prompt
+    system = request.messages[0]["content"]
+    assert "All findings in one request are on the same line." in system
+    assert "Mandatory: no deviation is allowed" in system
+    assert "do not cast a composite expression (Rule 10.8)" in system
+
+
 def test_auth_error_is_fatal_and_not_retried():
     handler, sent = scripted(error(401, "invalid key"))
 
diff --git a/tests/test_kinds.py b/tests/test_kinds.py
index 67400da..dcac286 100644
--- a/tests/test_kinds.py
+++ b/tests/test_kinds.py
@@ -1,7 +1,10 @@
-from rte_fixtures import RTE_HEADER, finding, rte_row
+import pytest
+from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_finding, misra_row, open_workbook
+from rte_fixtures import RTE_HEADER, finding, rte_row, workbook_bytes
 
-from llm_lab.kinds import KINDS, RTE
-from llm_lab.polyspace import Finding, column_name
+from llm_lab.kinds import KINDS, MISRA, RTE
+from llm_lab.kinds.misra import NO_CATEGORY, category_word, rule_key
+from llm_lab.polyspace import Finding, Prepared, column_name
 
 
 def cells(header: list, values: list):
@@ -25,6 +28,158 @@ def test_rte_priority_and_colors():
     assert (RTE.color("Red"), RTE.color("Orange"), RTE.color("기타")) == ("red", "orange", "gray")
 
 
+@pytest.mark.parametrize(
+    ("text", "key"),
+    [
+        ("10.3 The value of an expression", "10.3"),
+        ("Rule 10.3", "10.3"),
+        ("rule10.3", "10.3"),
+        (" 1.1 ", "1.1"),
+        ("Dir 4.1 Run-time failures", "D4.1"),
+        ("D4.1", "D4.1"),
+        ("Directive 4.1", "D4.1"),
+        ("See rule 10.3", None),  # 맨 앞에 번호가 없다
+        ("8 bits", None),  # 점으로 이은 숫자가 아니다
+        ("", None),
+    ],
+)
+def test_rule_key_takes_the_leading_rule_number(text, key):
+    assert rule_key(text) == key
+
+
+def test_rule_key_marks_directives_named_by_group():
+    assert rule_key("4.1 Run-time failures shall be minimized", directive=True) == "D4.1"
+
+
+@pytest.mark.parametrize(
+    ("text", "category"),
+    [
+        ("Category: Required", "Required"),
+        ("category:MANDATORY", "Mandatory"),
+        ("advisory", "Advisory"),
+        ("Category: -", ""),
+        ("", ""),
+    ],
+)
+def test_category_word(text, category):
+    assert category_word(text) == category
+
+
+def test_condition_sheet_is_found_by_its_columns_and_read_loosely():
+    data = workbook_bytes(
+        {
+            "MISRA_Result": [MISRA_HEADER],
+            "Notes": [["Guideline", "Remarks"]],  # mode 열이 없으면 조건 시트가 아니다
+            "Rules": CONDITION_ROWS,
+        }
+    )
+
+    prepared = MISRA.prepare(open_workbook(data))
+
+    assert prepared.data == {
+        "10.4": ["Required", True],
+        "12.1": ["Advisory", True],
+        "2.2": ["Advisory", False],
+        "D4.6": ["Mandatory", True],
+        "9.1": ["Disapplied", True],
+    }
+    assert prepared.note == "조건 시트: 규칙 5개"
+
+
+def test_condition_sheet_named_misra_wins_and_result_sheets_are_never_conditions():
+    data = workbook_bytes(
+        {
+            "Other_Result": [["Guideline", "Mode"], ["10.4", "required"]],
+            "Rules": [["Guideline", "Mode"], ["10.4", "advisory"]],
+            "MISRA rules": [["Guideline", "Mode"], ["10.4", "mandatory"]],
+        }
+    )
+
+    assert MISRA.prepare(open_workbook(data)).data == {"10.4": ["Mandatory", True]}
+
+
+def test_rule_numbers_that_collide_are_not_used():
+    # 숫자 칸으로 저장된 8.10은 8.1로 읽혀 진짜 8.1과 겹친다
+    rows = [["Guideline", "Mode"], [8.1, "required"], [8.10, "advisory"], [10.4, "mandatory"]]
+
+    prepared = MISRA.prepare(open_workbook(workbook_bytes({"Rules": rows})))
+
+    assert prepared.data == {"10.4": ["Mandatory", True]}
+    assert prepared.note == "조건 시트: 규칙 1개 · 번호가 겹친 1개는 결과 시트 분류를 씀"
+
+
+def test_without_condition_sheet_the_result_sheet_categories_are_used():
+    data = workbook_bytes({"MISRA_Result": [MISRA_HEADER], "Empty": [["Guideline", "Mode"]]})
+
+    assert MISRA.prepare(open_workbook(data)) == Prepared({}, "조건 시트 없음: 결과 시트 분류를 씀")
+
+
+CONDITIONS = {"10.4": ["Advisory", True], "2.2": ["Required", False]}
+
+
+def misra(values: list, context=CONDITIONS):
+    return MISRA.read_row(cells(MISRA_HEADER, values), 9, context)
+
+
+def test_misra_row_takes_the_condition_category_and_notes_a_difference():
+    found = misra(misra_row(category="Required"))
+
+    assert isinstance(found, Finding)
+    assert (found.row, found.color, found.line) == (9, "", 3)
+    assert found.information == "Category: Required"
+    assert (found.category, MISRA.label(found)) == ("Advisory", "Advisory")
+    assert found.note == "분류: 조건 시트 Advisory (결과 시트 Required)"
+
+
+def test_misra_row_of_a_disabled_rule_is_skipped():
+    assert misra(misra_row("2.2 There shall be no dead code")) == "적용 안 함 (조건 시트)"
+
+
+def test_misra_row_without_a_condition_uses_the_result_category():
+    found = misra(misra_row("12.1 Precedence", category="Mandatory"))
+    unknown = misra(misra_row("12.1 Precedence", category=""), context={})
+
+    assert (found.category, found.note) == ("Mandatory", "")
+    assert MISRA.label(unknown) == NO_CATEGORY
+
+
+def test_directive_rows_use_directive_conditions():
+    context = {"4.1": ["Advisory", True], "D4.1": ["Required", False]}
+
+    rule = misra(misra_row("4.1 Escape sequences", group="4 Character sets"), context)
+    directive = misra(misra_row("4.1 Run-time failures", group="Directive 4 Code design"), context)
+
+    assert rule.category == "Advisory"
+    assert directive == "적용 안 함 (조건 시트)"
+
+
+def test_misra_groups_rows_on_the_same_line_of_the_same_file():
+    first = misra_finding()
+    same_line = misra_finding(row=3, check="12.1 Precedence")
+
+    assert MISRA.group_key(first, "src/calc.c") == "src/calc.c:3"
+    assert MISRA.group_key(same_line, "src/calc.c") == "src/calc.c:3"
+    assert MISRA.group_key(misra_finding(line=4), "src/calc.c") == "src/calc.c:4"
+    assert MISRA.group_key(first, "src/other.c") == "src/other.c:3"
+    assert MISRA.group_key(misra_finding(line=None), "src/calc.c") == ""  # 혼자 보낸다
+    assert RTE.group_key(finding(), "src/calc.c") == ""
+
+
+def test_misra_order_colors_and_defaults():
+    assert MISRA.defaults == ("Mandatory", "Required")
+    ranks = [MISRA.rank(label) for label in ("Mandatory", "Required", "Advisory", NO_CATEGORY)]
+    assert ranks == [0, 1, 2, 3]
+    assert (MISRA.color("Mandatory"), MISRA.color("Disapplied")) == ("red", "gray")
+
+
+def test_misra_facts_send_the_category_instead_of_information():
+    facts = dict(MISRA.facts(misra_finding(category="Advisory")))
+
+    assert facts["category"] == "Advisory"
+    assert "information" not in facts
+    assert dict(MISRA.facts(misra_finding(category=NO_CATEGORY)))["category"] == ""
+
+
 def test_kinds_have_unique_names_and_defaults_among_choices():
     assert all(name == kind.name for name, kind in KINDS.items())
     for kind in KINDS.values():
diff --git a/tests/test_polyspace.py b/tests/test_polyspace.py
index 044f421..58e317c 100644
--- a/tests/test_polyspace.py
+++ b/tests/test_polyspace.py
@@ -2,9 +2,10 @@ import io
 from dataclasses import replace
 
 import pytest
+from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_row
 from rte_fixtures import RTE_HEADER, rte_excel, rte_row, workbook_bytes
 
-from llm_lab.kinds import RTE
+from llm_lab.kinds import MISRA, RTE
 from llm_lab.polyspace import Finding, Prepared, SheetError, read_sheet
 
 
@@ -85,6 +86,46 @@ def test_sheet_is_chosen_by_name_because_kinds_share_columns():
     assert "MISRA_Result (이름에 rte가 없음)" in message
 
 
+def test_misra_sheet_uses_the_condition_sheet():
+    data = workbook_bytes(
+        {
+            "RTE_Result": [RTE_HEADER, rte_row()],
+            "MISRA_Result": [
+                MISRA_HEADER,
+                misra_row("10.4 Operands", category="Advisory"),  # 조건 시트에서는 Required
+                misra_row("2.2 Dead code"),  # 조건 시트에서 꺼짐
+                misra_row("12.1 Precedence", category="Required"),  # 조건 시트에서는 Advisory
+                misra_row("21.3 Memory", category="Mandatory"),  # 조건 시트에 없음
+                misra_row("9.1 Init", category="Required"),  # 조건 시트 분류가 표준 밖이라 늘 남음
+            ],
+            "Rules": CONDITION_ROWS,
+        }
+    )
+
+    sheet = read_sheet(io.BytesIO(data), MISRA, MISRA.defaults)
+
+    assert sheet.sheet == "MISRA_Result"
+    assert [(f.row, f.category) for f in sheet.findings] == [
+        (2, "Required"),
+        (5, "Mandatory"),
+        (6, "Disapplied"),
+    ]
+    assert sheet.findings[0].note == "분류: 조건 시트 Required (결과 시트 Advisory)"
+    assert sheet.skipped == {"적용 안 함 (조건 시트)": 1, "Advisory (고르지 않음)": 1}
+    assert sheet.prepared.note == "조건 시트: 규칙 5개"
+
+
+def test_misra_sheet_without_condition_sheet_uses_its_own_categories():
+    data = workbook_bytes(
+        {"MISRA_Result": [MISRA_HEADER, misra_row(category="Advisory"), misra_row()]}
+    )
+
+    sheet = read_sheet(io.BytesIO(data), MISRA, ["Required", "Advisory"])
+
+    assert [f.category for f in sheet.findings] == ["Advisory", "Required"]
+    assert sheet.prepared.note == "조건 시트 없음: 결과 시트 분류를 씀"
+
+
 def test_non_integer_line_becomes_none():
     data = rte_excel(rte_row(line="12"), rte_row(line=12.0), rte_row(line="n/a"))
 
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q`
Expected: `ERROR tests/test_fixer.py`, `ERROR tests/test_kinds.py`, `ERROR tests/test_polyspace.py` — `ImportError: cannot import name 'MISRA' from 'llm_lab.kinds'`

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/kinds/__init__.py b/src/llm_lab/kinds/__init__.py
index b7f476c..0f9bda4 100644
--- a/src/llm_lab/kinds/__init__.py
+++ b/src/llm_lab/kinds/__init__.py
@@ -1,8 +1,9 @@
 """Polyspace 결과 시트 종류. 새 종류는 이 패키지에 파일 하나를 더하고 KINDS에 넣는다."""
 
 from llm_lab.kinds.base import Cell, Kind
+from llm_lab.kinds.misra import MISRA
 from llm_lab.kinds.rte import RTE
 
-KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE,)}
+KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE, MISRA)}
 
-__all__ = ["KINDS", "RTE", "Cell", "Kind"]
+__all__ = ["KINDS", "MISRA", "RTE", "Cell", "Kind"]
diff --git a/src/llm_lab/kinds/base.py b/src/llm_lab/kinds/base.py
index ffea29a..17cdc37 100644
--- a/src/llm_lab/kinds/base.py
+++ b/src/llm_lab/kinds/base.py
@@ -24,6 +24,10 @@ def nothing_to_prepare(workbook: Any) -> Prepared:
     return Prepared()
 
 
+def alone(finding: Finding, file_name: str) -> str:
+    return ""
+
+
 def default_facts(finding: Finding) -> list[tuple[str, str]]:
     """사용자 메시지에 넣을 지적 정보. File과 line은 fixer가 붙인다."""
     return [
@@ -62,6 +66,8 @@ class Kind:
     heading: str  # 사용자 메시지 첫 줄
     prompt_version: int  # 답 처리 방식을 바꾸면 올린다 (캐시 키)
     prepare: Callable[[Any], Prepared] = nothing_to_prepare  # 엑셀 전체에서 작업 정보 읽기
+    # 함께 보낼 행의 기준 (행, 짝지은 소스 이름) → 묶음 키. ''이면 혼자 보낸다
+    group_key: Callable[[Finding, str], str] = alone
     facts: Callable[[Finding], list[tuple[str, str]]] = default_facts
     # 사용자 메시지에서 Finding 목록 뒤, 코드 앞에 붙일 글 (작업 정보, 묶음의 행, 소스로)
     prompt_tail: Callable[[Any, list[Finding], SourceFile], str] = no_tail
diff --git a/src/llm_lab/kinds/misra.py b/src/llm_lab/kinds/misra.py
new file mode 100644
index 0000000..c290403
--- /dev/null
+++ b/src/llm_lab/kinds/misra.py
@@ -0,0 +1,197 @@
+"""MISRA C:2012 결과 시트와 조건 시트.
+
+결과 시트의 열은 RTE와 같고 ID만 없다. 규칙마다의 프로젝트 분류(Mode)와 적용 여부(Enabled)는
+엑셀의 조건 시트에서 작업마다 읽는다. 바뀔 수 있으므로 코드에 고정하지 않는다.
+"""
+
+from __future__ import annotations
+
+import re
+from typing import Any
+
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
+CATEGORIES = ("Mandatory", "Required", "Advisory")  # MISRA 표준 분류, 중요한 것부터
+NO_CATEGORY = "분류 없음"
+DISABLED = "적용 안 함 (조건 시트)"
+NO_CONDITIONS = "조건 시트 없음: 결과 시트 분류를 씀"
+# 조건 시트 Enabled 칸에서 꺼짐으로 보는 값 (공백 정리, 대소문자 무시). 그 밖은 켜짐이다
+OFF_WORDS = {"no", "n", "off", "false", "0", "disabled"}
+# 글 맨 앞의 규칙 번호: "10.3 …", "Rule 10.3", "Dir 4.1", "D4.1", "Directive 4.1"
+RULE = re.compile(r"\s*(?:rule\s*)?(dir(?:ective)?\s*|d\s*)?(\d+(?:\.\d+)+)", re.IGNORECASE)
+
+SYSTEM_PROMPT = """\
+You review Polyspace results for MISRA C:2012 in C code. \
+All findings in one request are on the same line. \
+For each finding, decide whether the code must change.
+- "check" gives the rule number and its headline, "detail" what Polyspace found, \
+and "category" the rule category this project uses.
+- Mandatory: no deviation is allowed, so fix it. If the code shown is not enough to fix it, \
+answer no_fix and say in reason that it must be fixed by hand and what is missing.
+- Required and Advisory: answer fix when a small change removes the violation \
+without changing behavior. Otherwise answer no_fix with a deviation rationale.
+When you fix:
+- Keep the behavior the same for every input. Typical fixes are explicit casts \
+to the intended essential type, U suffixes on unsigned constants, \
+added parentheses or braces, and splitting a statement.
+- Use the type names the code already uses, such as its typedefs.
+- Do not add new MISRA C:2012 violations. \
+For example, do not cast a composite expression (Rule 10.8); cast its operands.
+- One set of edits is shared by every finding you answer fix, so fix them together.
+- Change as little as possible. Do not touch lines unrelated to the findings.
+- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
+without the line-number prefix. "replacement" is the new text for those lines.
+- Keep each edit small: "original" holds only the lines you change. \
+Add one unchanged neighbouring line only when the changed line appears more than once.
+Do not guess definitions (macros, types, other functions) that are not shown. \
+If they matter, say so in reason.
+Write each "reason" in Korean, one to three sentences that a reviewer can paste \
+into the Polyspace comment of that finding. For fix, say what changed. \
+For no_fix, write why the code is acceptable as written.
+Answer with JSON only. "findings" has one entry for each numbered finding. \
+"edits" is [] when no finding is fix.
+"""
+
+
+def rule_key(text: str, directive: bool = False) -> str | None:
+    """글 맨 앞의 규칙 번호. 디렉티브면 앞에 D를 붙여 Rule 4.1과 Dir 4.1을 가른다.
+
+    맨 앞에 번호가 없으면 None이다. 규칙 문장 속의 숫자는 뽑지 않는다.
+    """
+    match = RULE.match(text)
+    if match is None:
+        return None
+    return ("D" if match.group(1) or directive else "") + match.group(2)
+
+
+def category_word(text: str) -> str:
+    """'Category: Required', 'required' → 'Required'. 분류 낱말이 없으면 ''."""
+    words = re.findall(r"[a-z]+", text.casefold())
+    return next((name for name in CATEGORIES if name.casefold() in words), "")
+
+
+def read_conditions(workbook: Any) -> Prepared:
+    """조건 시트에서 규칙 번호 → [분류, 켜짐]을 읽는다.
+
+    조건 시트는 이름이 _Result로 끝나지 않고 guideline·mode 열이 있는 시트다. 이름은 고정하지
+    않고, 여럿이면 이름에 misra가 든 시트를 쓴다.
+    """
+    found = []
+    for sheet in workbook.worksheets:
+        if sheet.title.casefold().endswith("_result"):
+            continue
+        header = find_header(sheet, ("guideline", "mode"))
+        if not isinstance(header, str):
+            found.append((sheet, *header))
+    if not found:
+        return Prepared({}, NO_CONDITIONS)
+    sheet, header_row, columns = next(
+        (item for item in found if "misra" in item[0].title.casefold()), found[0]
+    )
+    rules: dict[str, list[Any]] = {}
+    repeated: set[str] = set()
+    for _, cell in data_rows(sheet, header_row, columns):
+        key = rule_key(cell_text(cell("guideline")))
+        mode = " ".join(cell_text(cell("mode")).split())
+        if key is None or not mode:
+            continue
+        if key in rules:
+            # 숫자 칸으로 저장된 "8.10"은 8.1로 읽혀 진짜 8.1과 겹친다.
+            # 어느 쪽인지 알 수 없으므로 그 번호는 쓰지 않는다
+            repeated.add(key)
+        category = category_word(mode) or mode[:1].upper() + mode[1:]
+        rules[key] = [category, norm(cell("enabled")) not in OFF_WORDS]
+    for key in repeated:
+        del rules[key]
+    if not rules:
+        return Prepared({}, NO_CONDITIONS)
+    note = f"조건 시트: 규칙 {len(rules)}개"
+    if repeated:
+        note += f" · 번호가 겹친 {len(repeated)}개는 결과 시트 분류를 씀"
+    return Prepared(rules, note)
+
+
+def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
+    """모든 행이 대상이다. 다만 조건 시트에서 꺼진 규칙의 행은 뺀다.
+
+    분류는 조건 시트의 Mode가 먼저고, 없으면 결과 시트 information의 분류 낱말이다.
+    """
+    check = cell_text(cell("check"))
+    group = cell_text(cell("group"))
+    information = cell_text(cell("information"))
+    key = rule_key(check, "directive" in group.casefold())
+    rule = (context or {}).get(key) if key else None
+    if rule is not None and not rule[1]:
+        return DISABLED
+    reported = category_word(information)
+    category = rule[0] if rule is not None else reported or NO_CATEGORY
+    note = ""
+    if rule is not None and reported and reported != category:
+        note = f"분류: 조건 시트 {category} (결과 시트 {reported})"
+    return Finding(
+        row=row,
+        color="",
+        type=cell_text(cell("type")),
+        file=cell_text(cell("file")),
+        line=line_number(cell("line")),
+        check=check,
+        detail=cell_text(cell("detail")),
+        id=cell_text(cell("id")),
+        group=group,
+        information=information,
+        function=cell_text(cell("function")),
+        status=cell_text(cell("status")),
+        comment=cell_text(cell("comment")),
+        category=category,
+        note=note,
+    )
+
+
+def category_of(finding: Finding) -> str:
+    return finding.category or NO_CATEGORY
+
+
+def same_line(finding: Finding, file_name: str) -> str:
+    """같은 파일의 같은 줄에 걸린 행끼리 묶는다. 줄 번호가 없으면 혼자다."""
+    return f"{file_name}:{finding.line}" if finding.line is not None else ""
+
+
+def facts(finding: Finding) -> list[tuple[str, str]]:
+    """information 대신 실제 분류를 보낸다. 두 값이 다를 때 LLM이 헷갈리지 않게 하기 위해서다."""
+    category = "" if finding.category == NO_CATEGORY else finding.category
+    return [
+        ("TYPE", finding.type),
+        ("check", finding.check),
+        ("detail", finding.detail),
+        ("Group", finding.group),
+        ("category", category),
+        ("Function", finding.function),
+    ]
+
+
+MISRA = Kind(
+    name="misra",
+    title="MISRA C:2012",
+    sheet_word="misra",
+    required=("type", "file", "line", "check", "detail"),
+    read_row=read_row,
+    label=category_of,
+    choices=CATEGORIES,
+    defaults=("Mandatory", "Required"),
+    colors={"Mandatory": "red", "Required": "orange", "Advisory": "gray"},
+    system_prompt=SYSTEM_PROMPT,
+    heading="Polyspace MISRA C:2012 results",
+    prompt_version=1,
+    prepare=read_conditions,
+    group_key=same_line,
+    facts=facts,
+)
````

- [ ] **Step 4: 통과와 린트 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `366 passed`, 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/kinds tests/misra_fixtures.py tests/test_kinds.py tests/test_polyspace.py tests/test_fixer.py
```

```bash
git commit -m "feat: add the MISRA kind with live condition-sheet categories" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 화면과 웹: 종류 고르기, 멈춤, 배지·메모·묶음 표시 (`pages.py`, `web.py`)

**Files:**
- Modify: `src/llm_lab/kinds/base.py` (`auto_select` 필드, 기본 `always`)
- Modify: `src/llm_lab/web.py` (폼 필드 `kind`·`choice`, 400 확인, 묶음 키·작업 메모·작업 정보 저장, `POST /results/{id}/stop`, API의 `group`)
- Modify: `src/llm_lab/pages.py` (서비스 이름, 첫 화면 3단계 "분석할 시트", 작업 목록 "종류 · 대상", 결과 화면의 종류·메모·멈춤·이어서 처리·배지·메모·함께 보낸 행·diff 한 번만·자동 선택)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `KINDS`, `Kind`의 `label`·`color`·`rank`·`priority`·`group_key` (Task 1, 4), `Sheet.prepared` (Task 1), `Store.stop`, `USER_STOP`, `Job.kind`·`note`·`context`, `Row.grp` (Task 3)
- Produces:
  - `Kind.auto_select(finding) -> bool` (기본 `always`)
  - 폼: `kind`(기본 `rte`), `choice`(여러 개, 값은 `종류:대상`)
  - 경로: `POST /results/{job_id}/stop`
  - API 행에 `group`
  - `pages._selection(kind, rows) -> (conflicts, picked, manual)`

- [ ] **Step 1: 실패하는 테스트 쓰기**

````diff
diff --git a/tests/test_web.py b/tests/test_web.py
index 01c7d1f..398f806 100644
--- a/tests/test_web.py
+++ b/tests/test_web.py
@@ -1,16 +1,31 @@
+import json
 import socket
 import sqlite3
 import time
 from contextlib import closing
+from dataclasses import replace
 from pathlib import Path
 
 from fake_llm import MODEL, error, make_client, scripted
 from fastapi.testclient import TestClient
-from rte_fixtures import CALC_C, finding, fix_answer, rte_excel, rte_row
+from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_row
+from rte_fixtures import (
+    CALC_C,
+    DIVIDE_FIX,
+    REASON,
+    RTE_HEADER,
+    answer,
+    finding,
+    fix_answer,
+    rte_excel,
+    rte_row,
+    workbook_bytes,
+)
 
 from llm_lab import web
 from llm_lab.fixer import Edit, FixResult
 from llm_lab.folder_picker import PickerError
+from llm_lab.kinds import KINDS, RTE
 from llm_lab.sources import decode_source
 from llm_lab.store import NewRow, Store
 from llm_lab.worker import Worker
@@ -37,9 +52,9 @@ def source_folder(tmp_path) -> Path:
     return root
 
 
-def upload(client, excel: bytes, folder):
+def upload(client, excel: bytes, folder, kind="rte", choices=("rte:Red", "rte:Orange")):
     files = [("excel", ("rte.xlsx", excel))]
-    data = {"source_dir": str(folder)}
+    data = {"source_dir": str(folder), "kind": kind, "choice": list(choices)}
     return client.post("/results", files=files, data=data, follow_redirects=False)
 
 
@@ -55,7 +70,7 @@ def test_home_shows_upload_steps_and_menu(tmp_path):
 
     page = client.get("/").text
 
-    assert "<title>새 분석 · Polyspace RTE 수정 제안</title>" in page
+    assert "<title>새 분석 · Polyspace 수정 제안</title>" in page
     assert 'href="/results">작업 목록</a>' in page
     assert 'name="excel"' in page
     assert 'name="source_dir"' in page
@@ -81,7 +96,9 @@ def test_upload_processes_rows_and_shows_result_page(tmp_path):
     assert response.status_code == 303
     assert response.headers["location"] == "/results/1"
     page = client.get("/results/1").text
-    assert "<title>작업 1 결과 · rte.xlsx · Polyspace RTE 수정 제안</title>" in page
+    assert "<title>작업 1 결과 · rte.xlsx · Polyspace 수정 제안</title>" in page
+    assert "RTE · 시트 RTE_Result" in page
+    assert "제외: Gray Check 1" in page  # 작업 메모
     assert '<span class="badge green">완료</span>' in page
     for color, label in [("red", "Red"), ("orange", "Orange"), ("blue", "수정")]:
         assert f'<span class="badge {color}">{label}</span> <b>1</b>' in page
@@ -105,7 +122,8 @@ def test_result_json(tmp_path):
 
     data = client.get("/api/results/1").json()
 
-    assert data["state"] == "done"
+    assert (data["state"], data["kind"], data["note"]) == ("done", "rte", "제외: Gray Check 1")
+    assert [row["group"] for row in data["rows"]] == ["", ""]
     assert data["base_folder"] == str(folder)
     assert [(r["state"], r["decision"]) for r in data["rows"]] == [
         ("done", "fix"),
@@ -119,10 +137,13 @@ def test_results_list_page(tmp_path):
 
     page = client.get("/results").text
 
-    assert "<title>작업 목록 · Polyspace RTE 수정 제안</title>" in page
+    assert "<title>작업 목록 · Polyspace 수정 제안</title>" in page
     assert 'href="/results/1">결과 보기' in page
     assert "rte.xlsx" in page
-    assert '<span class="badge red">1</span> <span class="badge orange">1</span>' in page
+    assert (
+        'RTE <span class="badge red">Red 1</span> <span class="badge orange">Orange 1</span>'
+        in (page)
+    )
     assert '<span class="badge green">완료</span>' in page
 
 
@@ -191,6 +212,7 @@ def test_stopped_job_can_be_retried(tmp_path):
     assert '<span class="badge red">멈춤</span>' in page
     assert "[인증]" in page
     assert 'action="/results/1/retry"' in page
+    assert ">이어서 처리</button>" in page
 
     response = client.post("/results/1/retry", follow_redirects=False)
 
@@ -211,10 +233,136 @@ def test_excel_without_red_or_orange_rows(tmp_path):
 
     upload(client, rte_excel(rte_row("Gray Check")), source_folder(tmp_path))
 
-    assert "고칠 행 없음" in client.get("/results/1").text
+    assert "분석할 행이 없습니다" in client.get("/results/1").text
     assert sent == []
 
 
+def test_home_offers_each_kind_with_its_targets(tmp_path):
+    client, _, _ = make_service(tmp_path)
+
+    page = client.get("/").text
+
+    assert 'name="kind" value="rte" checked' in page
+    assert 'name="kind" value="misra">' in page
+    assert 'name="choice" value="rte:Red" checked' in page
+    assert 'name="choice" value="misra:Mandatory" checked' in page
+    assert 'name="choice" value="misra:Advisory">' in page  # Advisory는 처음에 꺼져 있다
+    assert '<div class="choices" data-choices="misra" hidden>' in page
+
+
+def test_unknown_kind_or_no_target_is_rejected(tmp_path):
+    client, _, sent = make_service(tmp_path)
+    folder = source_folder(tmp_path)
+
+    assert upload(client, EXCEL, folder, kind="cobol").status_code == 400
+    response = upload(client, EXCEL, folder, choices=["misra:Required"])  # RTE 대상이 없다
+
+    assert response.status_code == 400
+    assert "분석할 대상을 하나 이상 고르세요." in response.text
+    assert sent == []
+
+
+MISRA_EXCEL = workbook_bytes(
+    {
+        "RTE_Result": [RTE_HEADER, rte_row()],
+        "MISRA_Result": [
+            MISRA_HEADER,
+            misra_row("10.4 Operands", category="Advisory"),  # 조건 시트로 Required
+            misra_row("21.3 Memory", category="Mandatory"),  # 같은 줄 → 함께 보낸다
+            misra_row("2.2 Dead code"),  # 조건 시트에서 꺼짐
+            misra_row("12.1 Precedence", category="Required"),  # 조건 시트로 Advisory → 안 고름
+        ],
+        "Rules": CONDITION_ROWS,
+    }
+)
+
+
+def test_misra_job_sends_one_line_together_and_shows_categories(tmp_path):
+    reply = answer(("fix", REASON), ("fix", "같은 수정으로 함께 고침"), edits=[DIVIDE_FIX])
+    client, store, sent = make_service(tmp_path, reply)
+    choices = ["misra:Mandatory", "misra:Required", "rte:Red"]
+
+    response = upload(client, MISRA_EXCEL, source_folder(tmp_path), "misra", choices)
+
+    assert response.status_code == 303
+    assert len(sent) == 1  # 같은 줄의 두 행을 한 번에 보냈다
+    job = store.job(1)
+    assert (job.kind, job.sheet) == ("misra", "MISRA_Result")
+    assert (
+        job.note == "조건 시트: 규칙 5개 · 제외: 적용 안 함 (조건 시트) 1, Advisory (고르지 않음) 1"
+    )
+    assert json.loads(job.context)["10.4"] == ["Required", True]
+    page = client.get("/results/1").text
+    assert "MISRA C:2012 · 시트 MISRA_Result" in page
+    assert '<span class="badge red">Mandatory</span> <b>1</b>' in page
+    assert '<span class="badge orange">Required</span> <b>1</b>' in page
+    assert "분류: 조건 시트 Required (결과 시트 Advisory)" in page
+    assert "함께 보낸 행: 엑셀 3행" in page and "함께 보낸 행: 엑셀 2행" in page
+    assert page.count('<pre class="diff">') == 1  # 같은 묶음의 diff는 첫 행에만
+    assert "같은 묶음의 수정입니다 (엑셀 2행과 같음)." in page
+    assert "MISRA C:2012 <span" in client.get("/results").text
+
+    patch = client.post("/results/1/fixes.patch", data={"row": ["1", "2"]})
+
+    assert patch.status_code == 200  # 같은 수정은 하나로 합쳐 겹침이 아니다
+    assert patch.content.count(b"+    return (b != 0) ? a / b : 0;\n") == 1
+
+
+def test_running_job_can_be_stopped_and_continued(tmp_path):
+    queued = []
+    client, store, _ = make_service(tmp_path, fix_answer(), submit=queued.append)
+    upload(client, EXCEL, source_folder(tmp_path))
+    assert 'action="/results/1/stop"' in client.get("/results/1").text
+
+    response = client.post("/results/1/stop", follow_redirects=False)
+
+    assert response.status_code == 303
+    assert store.job(1).state == "stopped"
+    page = client.get("/results/1").text
+    assert "멈춘 작업입니다." in page
+    assert ">이어서 처리</button>" in page
+    assert 'http-equiv="refresh"' not in page
+
+    client.post("/results/1/retry")
+    for task in list(queued):
+        task()
+
+    assert store.job(1).state == "done"
+
+
+def test_stopped_job_still_gives_a_patch_for_finished_rows(tmp_path):
+    queued = []
+    client, _, _ = make_service(tmp_path, fix_answer(), submit=queued.append)
+    excel = rte_excel(rte_row("Orange Check"), rte_row("Orange Check", line=1, check="Overflow"))
+    upload(client, excel, source_folder(tmp_path))
+
+    queued[0]()  # 첫 묶음(엑셀 2행)만 처리하고
+    client.post("/results/1/stop")  # 멈춘다
+
+    assert 'action="/results/1/fixes.patch"' in client.get("/results/1").text
+    response = client.post("/results/1/fixes.patch", data={"row": ["1"]})
+    assert response.status_code == 200
+    assert b"+    return (b != 0) ? a / b : 0;\n" in response.content
+
+
+def test_kind_decides_which_fixes_are_picked_automatically(tmp_path, monkeypatch):
+    monkeypatch.setitem(KINDS, "rte", replace(RTE, auto_select=lambda f: f.color == "Red"))
+    header = {
+        "original": "int divide(int a, int b)",
+        "replacement": "int divide(int a, int b) /* 검토 */",
+    }
+    # Red(엑셀 3행)를 먼저 보내므로 첫 답이 Red 몫이다
+    client, _, _ = make_service(tmp_path, fix_answer(edits=[header]), fix_answer())
+    excel = rte_excel(rte_row("Orange Check"), rte_row("Red Check", line=1, check="Overflow"))
+    upload(client, excel, source_folder(tmp_path))
+
+    page = client.get("/results/1").text
+
+    assert 'name="row" value="2" data-auto="1"' in page
+    assert 'name="row" value="1" data-auto="0"' in page
+    assert "자동으로 고르지 않은 수정 1개는 직접 고르세요" in page
+
+
 def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
     monkeypatch.chdir(tmp_path)
     for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
````

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q`
Expected: `12 failed, 360 passed`. 실패는 `tests/test_web.py`의 새·바뀐 테스트들이다(서비스 이름, 작업 메모, 종류 폼, 멈춤, `TypeError: Kind.__init__() got an unexpected keyword argument 'auto_select'` 등).

- [ ] **Step 3: 구현**

````diff
diff --git a/src/llm_lab/kinds/base.py b/src/llm_lab/kinds/base.py
index 17cdc37..97e8d9e 100644
--- a/src/llm_lab/kinds/base.py
+++ b/src/llm_lab/kinds/base.py
@@ -28,6 +28,10 @@ def alone(finding: Finding, file_name: str) -> str:
     return ""
 
 
+def always(finding: Finding) -> bool:
+    return True
+
+
 def default_facts(finding: Finding) -> list[tuple[str, str]]:
     """사용자 메시지에 넣을 지적 정보. File과 line은 fixer가 붙인다."""
     return [
@@ -75,6 +79,7 @@ class Kind:
     answer_schema: Mapping[str, Any] = field(default_factory=lambda: FIX_SCHEMA)
     to_results: Callable[[dict[str, Any], Request], list[FixResult]] = edits_results
     timeout: float = FIX_TIMEOUT  # LLM 대기 시간(초)
+    auto_select: Callable[[Finding], bool] = always  # "수정 모두 선택"이 고를 행인지
 
     def rank(self, label: str) -> int:
         """중요한 대상일수록 작은 값. choices에 없는 배지 글자는 맨 뒤다."""
diff --git a/src/llm_lab/pages.py b/src/llm_lab/pages.py
index 9445296..d246598 100644
--- a/src/llm_lab/pages.py
+++ b/src/llm_lab/pages.py
@@ -8,11 +8,12 @@ from __future__ import annotations
 from collections import Counter
 from html import escape
 
+from llm_lab.kinds import KINDS, Kind
 from llm_lab.patch import APPLY_COMMAND, find_conflicts, pick_without_conflicts
 from llm_lab.sources import base_name
-from llm_lab.store import Job, Row
+from llm_lab.store import USER_STOP, Job, Row
 
-APP_NAME = "Polyspace RTE 수정 제안"
+APP_NAME = "Polyspace 수정 제안"
 JOB_STATES = {"running": "처리 중", "done": "완료", "stopped": "멈춤"}
 JOB_BADGES = {"running": "blue", "done": "green", "stopped": "red"}
 ROW_STATES = {
@@ -103,6 +104,10 @@ td.num, th.num { text-align: right; width: 64px; white-space: nowrap; }
 .progress div { height: 100%; background: var(--brand); }
 .notice { background: var(--red-bg); color: var(--red); border-radius: 6px; padding: 6px 10px;
   margin: 6px 0; }
+.notice.paused { background: var(--gray-bg); color: var(--text); }
+.kind { margin: 2px 0; }
+.choices { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 2px 0 6px 22px;
+  font-size: 13px; }
 .retry { margin: 6px 0 10px; }
 .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--line); margin: 0 0 8px; }
 .tabs button { font: inherit; font-weight: 700; color: var(--muted); background: none;
@@ -120,10 +125,14 @@ td.num, th.num { text-align: right; width: 64px; white-space: nowrap; }
 .list td.summary { color: var(--muted); max-width: 520px; overflow: hidden;
   text-overflow: ellipsis; white-space: nowrap; }
 .list tr.problem td.summary { color: var(--amber); }
+.list td.check { max-width: 320px; overflow: hidden; text-overflow: ellipsis;
+  white-space: nowrap; }
 .later { color: #cfd8e8; font-size: 12px; }
 .item { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--line);
   border-radius: 8px; margin-bottom: 8px; }
-.item.Red { border-left-color: var(--red); } .item.Orange { border-left-color: #f59e0b; }
+.item.band-red { border-left-color: var(--red); }
+.item.band-orange { border-left-color: #f59e0b; }
+.item.band-gray { border-left-color: #b9c3d3; }
 .item .head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 10px; }
 .item .check { font-weight: 700; }
 .item .where { color: var(--muted); }
@@ -134,6 +143,7 @@ td.num, th.num { text-align: right; width: 64px; white-space: nowrap; }
 .reason { background: #f3f7fe; border-left: 3px solid var(--brand); padding: 5px 9px;
   border-radius: 4px; margin: 0 0 6px; }
 .excluded { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
+.note { color: var(--amber); font-size: 12px; margin: 0 0 6px; }
 .problem-box { background: var(--amber-bg); border-left: 3px solid #e0a400; padding: 6px 10px;
   border-radius: 4px; }
 .problem-box .what { font-weight: 700; color: var(--amber); }
@@ -227,6 +237,14 @@ document.querySelectorAll('[data-copy]').forEach(button => button.addEventListen
     setTimeout(() => { button.textContent = '복사'; }, 1500);
   });
 }));
+// 새 분석: 고른 종류의 대상 체크 상자만 보인다
+const choiceBoxes = [...document.querySelectorAll('[data-choices]')];
+function showChoices(name) {
+  choiceBoxes.forEach(box => { box.hidden = box.dataset.choices !== name; });
+}
+document.querySelectorAll('input[name=kind]').forEach(radio => {
+  radio.addEventListener('change', () => showChoices(radio.value));
+});
 // 폴더 찾기: 서버가 같은 PC에서 윈도우 폴더 선택 창을 띄우고 고른 경로를 돌려준다
 const pick = document.getElementById('pick-folder');
 if (pick) pick.addEventListener('click', async () => {
@@ -278,8 +296,15 @@ def error_page(title: str, message: str) -> str:
     return page(title, body)
 
 
-def _badge(color: str, text: str) -> str:
-    return f'<span class="badge {color}">{escape(text)}</span>'
+def _badge(color: str, text: str, title: str = "") -> str:
+    tip = f' title="{escape(title)}"' if title else ""
+    return f'<span class="badge {color}"{tip}>{escape(text)}</span>'
+
+
+def _label_counts(kind: Kind, rows: list[Row]) -> list[tuple[str, int]]:
+    """배지 글자(대상)별 행 수. 중요한 대상부터."""
+    counts = Counter(kind.label(row.finding) for row in rows)
+    return sorted(counts.items(), key=lambda item: (kind.rank(item[0]), item[0]))
 
 
 def _progress(rows: list[Row]) -> tuple[int, int]:
@@ -301,17 +326,46 @@ def _jobs_table(items: list[tuple[Job, list[Row]]]) -> str:
         return '<div class="card table"><p class="empty">아직 작업이 없습니다.</p></div>'
     lines = []
     for job, rows in items:
-        colors = Counter(row.finding.color for row in rows)
+        kind = KINDS[job.kind]
+        labels = " ".join(
+            _badge(kind.color(label), f"{label} {count}")
+            for label, count in _label_counts(kind, rows)
+        )
         when = job.created_at[5:16].replace("T", " ")
         lines.append(
             f"<tr><td>{job.id}</td><td>{escape(when)}</td><td>{escape(job.excel_name)}</td>"
-            f"<td>{_badge('red', str(colors['Red']))} {_badge('orange', str(colors['Orange']))}"
-            f"</td><td>{_job_badge(job, rows)}</td>"
+            f"<td>{escape(kind.title)} {labels}</td><td>{_job_badge(job, rows)}</td>"
             f'<td><a href="/results/{job.id}">결과 보기 →</a></td></tr>'
         )
     return (
         '<div class="card table"><table><tr><th>번호</th><th>시각</th><th>엑셀</th>'
-        "<th>Red / Orange</th><th>상태</th><th></th></tr>" + "".join(lines) + "</table></div>"
+        "<th>종류 · 대상</th><th>상태</th><th></th></tr>" + "".join(lines) + "</table></div>"
+    )
+
+
+def _kind_step() -> str:
+    """분석할 시트(종류) 라디오와 종류별 대상 체크 상자. 처음에는 첫 종류(RTE)를 고른다."""
+    blocks = []
+    for number, kind in enumerate(KINDS.values()):
+        first = number == 0
+        boxes = "".join(
+            f'<label><input type="checkbox" name="choice" '
+            f'value="{escape(kind.name)}:{escape(label)}"'
+            f"{' checked' if label in kind.defaults else ''}> {escape(label)}</label>"
+            for label in kind.choices
+        )
+        blocks.append(
+            f'<div class="kind"><label><input type="radio" name="kind" '
+            f'value="{escape(kind.name)}"{" checked" if first else ""}> '
+            f"<b>{escape(kind.title)}</b></label>"
+            f'<div class="choices" data-choices="{escape(kind.name)}"{"" if first else " hidden"}>'
+            f"{boxes}</div></div>"
+        )
+    return (
+        '<div class="step"><div class="num">3</div><div><h3>분석할 시트</h3>'
+        '<p class="hint">고른 종류의 결과 시트를 읽고, 체크한 대상만 LLM에 보냅니다.</p>'
+        + "".join(blocks)
+        + "</div></div>"
     )
 
 
@@ -320,7 +374,7 @@ def index_page(recent: list[tuple[Job, list[Row]]], recent_dirs: list[str]) -> s
     form = (
         '<form class="card" method="post" action="/results" enctype="multipart/form-data">'
         '<div class="step"><div class="num">1</div><div><h3>Polyspace 결과 엑셀</h3>'
-        '<p class="hint">이름이 _Result로 끝나는 RTE 시트가 있는 .xlsx 파일 하나</p>'
+        '<p class="hint">이름이 _Result로 끝나는 결과 시트가 있는 .xlsx 파일 하나</p>'
         '<input type="file" name="excel" accept=".xlsx" required></div></div>'
         '<div class="step"><div class="num">2</div><div><h3>소스 폴더</h3>'
         '<p class="hint">소스 코드가 들어 있는 최상위 폴더. "폴더 찾기…"로 고르거나 경로를 '
@@ -331,13 +385,14 @@ def index_page(recent: list[tuple[Job, list[Row]]], recent_dirs: list[str]) -> s
         '<button class="btn plain" type="button" id="pick-folder">폴더 찾기…</button></div>'
         '<p class="hint" id="pick-note"></p>'
         f'<datalist id="recent-dirs">{options}</datalist></div></div>'
-        '<div class="step"><div class="num">3</div><div><h3>분석 시작</h3>'
+        + _kind_step()
+        + '<div class="step"><div class="num">4</div><div><h3>분석 시작</h3>'
         '<p class="hint">결과 화면으로 넘어가고, 행이 하나씩 처리되는 모습을 볼 수 있습니다</p>'
         '<button class="btn primary" type="submit">분석 시작</button></div></div></form>'
     )
     body = (
         "<h1>새 분석</h1>"
-        '<p class="sub">Polyspace 결과 엑셀과 소스 폴더를 주면 Red·Orange 행마다 '
+        '<p class="sub">Polyspace 결과 엑셀과 소스 폴더를 주면 고른 시트의 행마다 '
         "LLM이 수정안을 만듭니다.</p>"
         + form
         + '<h2>최근 작업 <a href="/results" style="font-size:12px;font-weight:400">'
@@ -404,8 +459,11 @@ def _file_label(job: Job, row: Row) -> str:
     return base_name(row.finding.file)
 
 
-def _selection(rows: list[Row]) -> tuple[dict[int, set[int]], set[int]]:
-    """수정 행끼리의 충돌과, "수정 모두 선택" 때 고를 행 (Red 먼저, 그다음 엑셀 행 순)."""
+def _selection(kind: Kind, rows: list[Row]) -> tuple[dict[int, set[int]], set[int], set[int]]:
+    """수정 행끼리의 충돌, "수정 모두 선택" 때 고를 행, 종류가 자동으로 고르지 않는 수정 행.
+
+    고르는 순서는 중요한 대상 먼저, 그다음 엑셀 행 순서다.
+    """
     by_id = {row.id: row for row in rows}
     fixable = {
         row.id: (row.file_name or "", list(row.result.edits))
@@ -413,11 +471,12 @@ def _selection(rows: list[Row]) -> tuple[dict[int, set[int]], set[int]]:
         if row.file_name and row.result is not None and row.result.decision == "fix"
     }
     conflicts = find_conflicts(fixable)
+    manual = {row_id for row_id in fixable if not kind.auto_select(by_id[row_id].finding)}
     order = sorted(
-        fixable,
-        key=lambda row_id: (by_id[row_id].finding.color != "Red", by_id[row_id].finding.row),
+        (row_id for row_id in fixable if row_id not in manual),
+        key=lambda row_id: (kind.priority(by_id[row_id].finding), by_id[row_id].finding.row),
     )
-    return conflicts, pick_without_conflicts(order, conflicts)
+    return conflicts, pick_without_conflicts(order, conflicts), manual
 
 
 def _partner(row: Row, conflicts: dict[int, set[int]], picked: set[int], by_id: dict[int, Row]):
@@ -426,15 +485,13 @@ def _partner(row: Row, conflicts: dict[int, set[int]], picked: set[int], by_id:
     return by_id[chosen[0]] if chosen else None
 
 
-def _chips(job: Job, rows: list[Row]) -> str:
-    colors = Counter(row.finding.color for row in rows)
+def _chips(kind: Kind, job: Job, rows: list[Row]) -> str:
     kinds = Counter(_kind(row) for row in rows)
     parts = [
-        f"<span>{_badge('red', 'Red')} <b>{colors['Red']}</b></span>",
-        f"<span>{_badge('orange', 'Orange')} <b>{colors['Orange']}</b></span>",
-        _badge("gray", f"제외 {job.skipped}"),
-        '<span class="sep">|</span>',
+        f"<span>{_badge(kind.color(label), label)} <b>{count}</b></span>"
+        for label, count in _label_counts(kind, rows)
     ]
+    parts += [_badge("gray", f"제외 {job.skipped}"), '<span class="sep">|</span>']
     for kind in ("fix", "nofix", "problem", "waiting"):
         if kind != "waiting" or kinds[kind]:
             label = _badge(KIND_BADGES[kind], KIND_LABELS[kind])
@@ -497,6 +554,7 @@ def _status_badge(row: Row) -> str:
 
 
 def _overview_row(
+    sheet_kind: Kind,
     job: Job,
     row: Row,
     finished: bool,
@@ -531,11 +589,12 @@ def _overview_row(
         title = ROW_STATES.get(row.state, row.state)
         summary_html = escape(title)
     line = finding.line if finding.line is not None else "?"
-    color = _badge("red" if finding.color == "Red" else "orange", finding.color)
+    label = sheet_kind.label(finding)
+    badge = _badge(sheet_kind.color(label), label, finding.note)
     css = ' class="problem"' if kind == "problem" else ""
     return (
-        f'<tr data-kind="{kind}"{css}><td>{box}</td><td>{finding.row}</td><td>{color}</td>'
-        f"<td>{escape(finding.check)}</td>"
+        f'<tr data-kind="{kind}"{css}><td>{box}</td><td>{finding.row}</td><td>{badge}</td>'
+        f'<td class="check" title="{escape(finding.check)}">{escape(finding.check)}</td>'
         f'<td class="mono">{escape(_file_label(job, row))}:{line}</td>'
         f"<td>{_status_badge(row)}</td>"
         f'<td class="summary" title="{escape(title)}">{summary_html}</td>'
@@ -543,9 +602,11 @@ def _overview_row(
     )
 
 
-def _overview(job: Job, rows: list[Row], finished: bool, conflicts, picked) -> str:
+def _overview(kind: Kind, job: Job, rows: list[Row], finished: bool, conflicts, picked) -> str:
     by_id = {row.id: row for row in rows}
-    lines = "".join(_overview_row(job, row, finished, conflicts, picked, by_id) for row in rows)
+    lines = "".join(
+        _overview_row(kind, job, row, finished, conflicts, picked, by_id) for row in rows
+    )
     return (
         _groups(job, rows) + '<div class="card table list"><table><tr><th style="width:26px"></th>'
         '<th style="width:44px">행</th><th style="width:64px">종류</th><th>검사</th>'
@@ -555,27 +616,36 @@ def _overview(job: Job, rows: list[Row], finished: bool, conflicts, picked) -> s
 
 
 def _detail_card(
+    kind: Kind,
     job: Job,
     row: Row,
     finished: bool,
     conflicts: dict[int, set[int]],
     picked: set[int],
     by_id: dict[int, Row],
+    groups: dict[str, list[Row]],
 ) -> str:
     finding = row.finding
+    label = kind.label(finding)
+    members = groups.get(row.grp, []) if row.grp else []
     where = f"{_file_label(job, row)}:{finding.line if finding.line is not None else '?'}"
     if finding.function:
         where += f" · {finding.function}()"
     mirror = ""
     if finished and row.id in conflicts:
         mirror = f'<label class="pick"><input type="checkbox" data-mirror="{row.id}"> 적용</label>'
-    color = _badge("red" if finding.color == "Red" else "orange", finding.color)
+    badge = _badge(kind.color(label), label)
     head = (
-        f'<div class="head">{color}<span class="check">{escape(finding.check)}</span>'
+        f'<div class="head">{badge}<span class="check">{escape(finding.check)}</span>'
         f'<span class="where mono">{escape(where)}</span>'
         f'<div class="right">{_status_badge(row)}{mirror}</div></div>'
     )
     body = [f'<p class="detail">엑셀 {finding.row}행 · {escape(finding.detail)}</p>']
+    if finding.note:
+        body.append(f'<p class="note">{escape(finding.note)}</p>')
+    others = [str(member.finding.row) for member in members if member.id != row.id]
+    if others:
+        body.append(f'<p class="excluded">함께 보낸 행: 엑셀 {", ".join(others)}행</p>')
     if row.result is not None:
         partner = _partner(row, conflicts, picked, by_id) if row.id not in picked else None
         if row.result.decision == "fix" and partner is not None:
@@ -585,13 +655,23 @@ def _detail_card(
                 "다시 분석하면 남은 문제만 다시 고칩니다.</p>"
             )
         body.append(f'<div class="reason">{escape(row.result.reason)}</div>')
-        if row.result.diff:
+        # 같은 묶음의 수정 행은 수정이 똑같으므로 diff는 묶음의 첫 수정 행에만 보인다
+        first_fix = next(
+            (m for m in members if m.result is not None and m.result.decision == "fix"), None
+        )
+        if first_fix is not None and first_fix.id != row.id:
+            body.append(
+                f'<p class="excluded">같은 묶음의 수정입니다 '
+                f"(엑셀 {first_fix.finding.row}행과 같음).</p>"
+            )
+        elif row.result.diff:
             body.append(_diff(row.result.diff))
     elif _kind(row) == "problem":
         summary, action, detail = _explain(row)
         more = ""
         if row.state == "error":
-            log = f"대화 기록: private/llm-logs/ 안의 …_job{job.id}_row{finding.row}.md"
+            log_row = min((m.finding.row for m in members), default=finding.row)
+            log = f"대화 기록: private/llm-logs/ 안의 …_job{job.id}_row{log_row}.md"
             text = "\n".join(part for part in (detail, log) if part)
             more = f"<details><summary>자세히 (개발자용)</summary>{escape(text)}</details>"
         todo = f'<div class="todo">할 일: {escape(action)}</div>' if action else ""
@@ -599,7 +679,7 @@ def _detail_card(
             f'<div class="problem-box"><div class="what">{escape(summary)}</div>{todo}{more}</div>'
         )
     return (
-        f'<div class="item {finding.color}" id="row-{row.id}" data-kind="{_kind(row)}">'
+        f'<div class="item band-{kind.color(label)}" id="row-{row.id}" data-kind="{_kind(row)}">'
         f'{head}<div class="body">{"".join(body)}</div></div>'
     )
 
@@ -624,48 +704,69 @@ def _howto(base: str | None) -> str:
 
 
 def job_page(job: Job, rows: list[Row], base: str | None) -> str:
+    kind = KINDS[job.kind]
     finished = job.state != "running"
     when = job.created_at[5:16].replace("T", " ")
     body = (
         f"<h1>작업 {job.id} · {escape(job.excel_name)} {_job_badge(job, rows)}</h1>"
-        f'<p class="sub">시트 {escape(job.sheet)} · {escape(when)} 시작'
+        f'<p class="sub">{escape(kind.title)} · 시트 {escape(job.sheet)} · {escape(when)} 시작'
         + (f" · 소스 폴더 {escape(job.source_root)}" if job.source_root else "")
         + "</p>"
     )
+    if job.note:
+        body += f'<p class="sub">{escape(job.note)}</p>'
     if job.state == "running":
         done, total = _progress(rows)
         percent = round(done * 100 / total) if total else 100
         body += (
             f'<div class="progress"><div style="width:{percent}%"></div></div>'
             f'<p class="sub">{done}/{total} 처리 · 5초마다 새로 고칩니다</p>'
+            f'<form class="retry" method="post" action="/results/{job.id}/stop">'
+            '<button class="btn plain" type="submit">멈춤</button></form>'
         )
-    if job.state == "stopped":
+    if job.state == "stopped" and job.message == USER_STOP:
+        body += (
+            '<div class="notice paused">멈춘 작업입니다. '
+            "'이어서 처리'를 누르면 남은 행을 처리합니다.</div>"
+        )
+    elif job.state == "stopped":
         body += f'<div class="notice">작업이 멈췄습니다: {escape(job.message)}</div>'
     if finished and (job.state == "stopped" or any(row.state == "error" for row in rows)):
+        label = "이어서 처리" if job.state == "stopped" else "오류 행 다시 시도"
         body += (
             f'<form class="retry" method="post" action="/results/{job.id}/retry">'
-            '<button class="btn plain" type="submit">오류 행 다시 시도</button></form>'
+            f'<button class="btn plain" type="submit">{label}</button></form>'
         )
-    body += _chips(job, rows)
+    body += _chips(kind, job, rows)
     title = f"작업 {job.id} 결과 · {job.excel_name}"
     if not rows:
-        body += (
-            '<div class="card"><p class="empty">고칠 행 없음 (Red/Orange 행이 없습니다)</p></div>'
-        )
+        body += '<div class="card"><p class="empty">분석할 행이 없습니다.</p></div>'
         return page(title, body, refresh=not finished)
-    conflicts, picked = _selection(rows) if finished else ({}, set())
+    conflicts, picked, manual = _selection(kind, rows) if finished else ({}, set(), set())
     by_id = {row.id: row for row in rows}
-    cards = "".join(_detail_card(job, row, finished, conflicts, picked, by_id) for row in rows)
+    groups: dict[str, list[Row]] = {}
+    for row in rows:
+        if row.grp:
+            groups.setdefault(row.grp, []).append(row)
+    for members in groups.values():
+        members.sort(key=lambda member: member.finding.row)
+    cards = "".join(
+        _detail_card(kind, job, row, finished, conflicts, picked, by_id, groups) for row in rows
+    )
+    overview = _overview(kind, job, rows, finished, conflicts, picked)
     panels = (
         '<div class="tabs"><button type="button" data-tab="overview" class="on">한눈에 보기'
         '</button><button type="button" data-tab="detail">자세히 보기 (diff)</button></div>'
         + _filters(rows)
-        + f'<div data-panel="overview">{_overview(job, rows, finished, conflicts, picked)}</div>'
+        + f'<div data-panel="overview">{overview}</div>'
         + f'<div data-panel="detail" hidden>{cards}</div>'
     )
     if finished and conflicts:
-        later = len(conflicts) - len(picked)
+        later = len(conflicts) - len(picked) - len(manual)
         note = f'<span class="later">같은 줄 겹침으로 {later}개는 다음 차례</span>' if later else ""
+        if manual:
+            hint = f"자동으로 고르지 않은 수정 {len(manual)}개는 직접 고르세요"
+            note += f'<span class="later">{hint}</span>'
         body += (
             f'<form method="post" action="/results/{job.id}/fixes.patch">{panels}'
             f"{_howto(base)}"
diff --git a/src/llm_lab/web.py b/src/llm_lab/web.py
index bfb67d8..1b713ca 100644
--- a/src/llm_lab/web.py
+++ b/src/llm_lab/web.py
@@ -1,9 +1,10 @@
-"""Polyspace RTE 수정 제안 웹 서비스 (llm-fix-server)."""
+"""Polyspace 수정 제안 웹 서비스 (llm-fix-server)."""
 
 from __future__ import annotations
 
 import argparse
 import io
+import json
 import socket
 import sys
 import threading
@@ -21,10 +22,10 @@ from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Resp
 from llm_lab.client import get_client
 from llm_lab.config import ConfigError, load_settings
 from llm_lab.folder_picker import PickerError, pick_folder
-from llm_lab.kinds import RTE
+from llm_lab.kinds import KINDS, Kind
 from llm_lab.pages import error_page, index_page, job_page, results_page
 from llm_lab.patch import FilePatch, PatchError, base_folder, build_patch, relative_path
-from llm_lab.polyspace import Finding, SheetError, read_sheet
+from llm_lab.polyspace import Finding, Sheet, SheetError, read_sheet
 from llm_lab.sources import Match, load_from_folder
 from llm_lab.store import Job, NewRow, Row, Store, StoreError
 from llm_lab.worker import Worker, daemon_pool
@@ -38,10 +39,20 @@ RECENT_JOBS = 10  # 첫 화면에 보여줄 최근 작업 수
 LISTED_JOBS = 100  # 작업 목록 화면에 보여줄 작업 수
 
 
-def _new_row(finding: Finding, match: Match) -> NewRow:
+def _new_row(kind: Kind, finding: Finding, match: Match) -> NewRow:
     if match.source is None:
         return NewRow(finding, None, "no_source", match.reason)
-    return NewRow(finding, match.source.name, "pending")
+    name = match.source.name
+    return NewRow(finding, name, "pending", grp=kind.group_key(finding, name))
+
+
+def _job_note(sheet: Sheet) -> str:
+    """작업 메모 한 줄: 종류가 엑셀 전체에서 읽은 메모와, 뺀 행의 이유별 개수."""
+    parts = [sheet.prepared.note] if sheet.prepared.note else []
+    if sheet.skipped:
+        skipped = ", ".join(f"{reason} {count}" for reason, count in sheet.skipped.items())
+        parts.append(f"제외: {skipped}")
+    return " · ".join(parts)
 
 
 def _base(job: Job, rows: list[Row]) -> str | None:
@@ -61,6 +72,7 @@ def _row_json(row: Row) -> dict[str, Any]:
         "id": row.id,
         "state": row.state,
         "error": row.error,
+        "group": row.grp,
         "finding": asdict(row.finding),
         "decision": row.result.decision if row.result else None,
         "reason": row.result.reason if row.result else None,
@@ -112,8 +124,22 @@ def create_app(store: Store, worker: Worker) -> FastAPI:
 
     @app.post("/results")
     def create_job(
-        excel: Annotated[UploadFile, File()], source_dir: Annotated[str, Form()] = ""
+        excel: Annotated[UploadFile, File()],
+        source_dir: Annotated[str, Form()] = "",
+        kind: Annotated[str, Form()] = "rte",
+        choice: Annotated[list[str] | None, Form()] = None,
     ) -> Response:
+        sheet_kind = KINDS.get(kind)
+        if sheet_kind is None:
+            message = f"분석할 시트 종류를 다시 고르세요: {kind}"
+            return HTMLResponse(error_page("알 수 없는 종류", message), status_code=400)
+        # 체크 상자 값은 "종류:대상"이다. 고른 종류의 것만, 그 종류의 대상인 것만 쓴다
+        prefix = f"{sheet_kind.name}:"
+        picked = {value.removeprefix(prefix) for value in choice or [] if value.startswith(prefix)}
+        selected = picked & set(sheet_kind.choices)
+        if sheet_kind.choices and not selected:
+            message = "분석할 대상을 하나 이상 고르세요."
+            return HTMLResponse(error_page("분석할 대상이 없음", message), status_code=400)
         root = _source_root(source_dir)
         if root is None or not root.is_dir():
             message = (
@@ -121,18 +147,26 @@ def create_app(store: Store, worker: Worker) -> FastAPI:
             ) + " 탐색기 주소창의 경로를 그대로 붙여넣으면 됩니다."
             return HTMLResponse(error_page("소스 폴더를 찾을 수 없음", message), status_code=400)
         try:
-            sheet = read_sheet(io.BytesIO(excel.file.read()), RTE, RTE.defaults)
+            sheet = read_sheet(io.BytesIO(excel.file.read()), sheet_kind, selected)
         except SheetError as exc:
             return HTMLResponse(error_page("엑셀을 읽을 수 없음", str(exc)), status_code=400)
         # 엑셀에 나온 파일만 폴더에서 찾아 읽는다. 읽은 내용은 작업에 함께 저장해
         # 나중에 소스가 바뀌어도 결과와 패치는 분석 당시 기준으로 남는다.
         matches, files = load_from_folder(root, [f.file for f in sheet.findings])
-        rows = [_new_row(finding, matches[finding.file]) for finding in sheet.findings]
+        rows = [_new_row(sheet_kind, finding, matches[finding.file]) for finding in sheet.findings]
         used = {row.file_name for row in rows if row.file_name}
         files = [source for source in files if source.name in used]
-        skipped = sum(sheet.skipped.values())
+        data = sheet.prepared.data
         job_id = store.create_job(
-            excel.filename or "", sheet.sheet, skipped, files, rows, source_root=str(root)
+            excel.filename or "",
+            sheet.sheet,
+            sum(sheet.skipped.values()),
+            files,
+            rows,
+            source_root=str(root),
+            kind=sheet_kind.name,
+            note=_job_note(sheet),
+            context="" if data is None else json.dumps(data, ensure_ascii=False),
         )
         worker.start(job_id)
         return RedirectResponse(f"/results/{job_id}", status_code=303)
@@ -176,6 +210,12 @@ def create_app(store: Store, worker: Worker) -> FastAPI:
             headers={"Content-Disposition": 'attachment; filename="fixes.patch"'},
         )
 
+    @app.post("/results/{job_id}/stop")
+    def stop(job_id: int) -> Response:
+        job_or_404(job_id)
+        store.stop(job_id)
+        return RedirectResponse(f"/results/{job_id}", status_code=303)
+
     @app.post("/results/{job_id}/retry")
     def retry(job_id: int) -> Response:
         job_or_404(job_id)
@@ -209,7 +249,7 @@ def open_when_ready(url: str, port: int, timeout: float = 15.0) -> None:
 
 def main(argv: list[str] | None = None) -> int:
     parser = argparse.ArgumentParser(
-        prog="llm-fix-server", description="Polyspace RTE 수정 제안 서비스"
+        prog="llm-fix-server", description="Polyspace 수정 제안 서비스"
     )
     parser.add_argument("--port", type=int, default=8000, help="접속 포트 (기본 8000)")
     parser.add_argument(
````

- [ ] **Step 4: 통과와 린트 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `372 passed`, 린트 통과

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/kinds/base.py src/llm_lab/web.py src/llm_lab/pages.py tests/test_web.py
```

```bash
git commit -m "feat: choose the sheet kind on the home page, stop jobs, show groups and notes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 문서와 회사 PC 확인 (`README.md`, RTE 설계 문서, `todo.md`)

**Files:**
- Modify: `README.md` (서비스 설명, 종류·대상 고르기, 조건 시트, 묶음, 멈춤, 기록 파일)
- Modify: `docs/superpowers/specs/2026-10-07-rte-fixer-design.md` (이후 변경 안내 한 줄)
- Modify: `todo.md` (회사 PC 확인 절 하나)

**Interfaces:**
- Consumes: Task 1~5의 동작
- Produces: 문서만 바뀐다.

- [ ] **Step 1: README와 RTE 설계 문서 고치기**

````diff
diff --git a/README.md b/README.md
index 8cd6246..dce0cdb 100644
--- a/README.md
+++ b/README.md
@@ -1,6 +1,6 @@
 # llm-lab
 
-회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace RTE 수정 제안 서비스입니다.
+회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace 수정 제안 서비스(RTE·MISRA)입니다.
 
 ## 준비
 
@@ -58,26 +58,30 @@ uv run llm-probe --context --output probe-result-context.txt
 
 ## 수정 제안 서비스 (llm-fix-server)
 
-Polyspace Code Prover 결과 엑셀의 RTE 시트에서 Red/Orange 행마다 회사 LLM이 "수정" 또는 "수정 불필요"를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.
+Polyspace 결과 엑셀의 RTE 시트(Red/Orange) 또는 MISRA C:2012 시트에서 행마다 회사 LLM이 "수정" 또는 "수정 불필요"를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.
 
 ```bash
 uv run llm-fix-server
 ```
 
 1. 서버가 켜지면 브라우저가 `http://127.0.0.1:8000/`을 저절로 엽니다. 같은 PC에서만 열립니다. 포트를 바꾸려면 `--port 8080`, 브라우저를 열지 않으려면 `--no-browser`를 붙입니다.
-2. 엑셀(.xlsx)을 고르고, 소스 코드가 들어 있는 최상위 폴더를 정한 뒤 "분석 시작"을 누릅니다.
-   - 엑셀은 이름이 `_Result`로 끝나고 `TYPE`, `File`, `line`, `check`, `detail` 열이 있는 시트를 읽습니다.
+2. 엑셀(.xlsx)을 고르고, 소스 코드가 들어 있는 최상위 폴더와 분석할 시트를 정한 뒤 "분석 시작"을 누릅니다.
+   - "분석할 시트"에서 RTE나 MISRA를 고르고 분석할 대상을 체크합니다. RTE는 Red·Orange, MISRA는 Mandatory·Required·Advisory이고, MISRA는 처음에 Mandatory와 Required만 체크되어 있습니다.
+   - 엑셀은 이름이 `_Result`로 끝나고 종류 이름(`rte`, `misra`)이 들어간 시트를 읽습니다. 그 시트에 `TYPE`, `File`, `line`, `check`, `detail` 열이 있어야 합니다.
+   - MISRA는 엑셀의 조건 시트(이름은 상관없고 `Guideline`, `Mode` 열이 있는 시트)에서 규칙마다 분류(`Mode`)와 적용 여부(`Enabled`)를 작업마다 읽습니다. 꺼진 규칙은 빼고, 분류는 조건 시트를 따릅니다. 조건 시트가 없으면 결과 시트의 `Category`를 씁니다.
    - "폴더 찾기…" 버튼을 누르면 윈도우 폴더 선택 창이 뜨고, 고른 폴더 경로가 칸에 채워집니다. 창이 안 보이면 작업 표시줄을 확인하세요.
    - 경로를 직접 붙여넣어도 됩니다(탐색기 주소창에서 복사, 따옴표가 붙어 있어도 됨).
    - 엑셀에 나온 파일만 그 폴더 안에서 찾아 읽습니다. 같은 이름 파일이 여러 개면 엑셀 경로와 폴더 구조가 가장 길게 맞는 것을 고르고, `.git`처럼 점으로 시작하는 폴더는 보지 않습니다.
    - 패치도 그 폴더 기준으로 만들어지므로 그 폴더에서 적용합니다.
    - 최근에 쓴 폴더는 입력 칸에서 목록으로 고를 수 있습니다.
 3. 처리 중에는 결과 페이지가 진행 막대와 함께 5초마다 새로 고쳐집니다. 결과는 탭 두 개로 봅니다.
+   - MISRA는 같은 파일의 같은 줄에 걸린 지적을 묶어 LLM에 한 번에 보냅니다. 판단과 이유는 행마다 받고, 수정은 묶음이 함께 씁니다.
+   - 중요한 대상(Red, Mandatory)부터 처리합니다. "멈춤"을 누르면 그때까지 처리된 행으로 패치를 받을 수 있고, "이어서 처리"로 남은 행을 계속합니다.
    - "한눈에 보기": 맨 위 개수(Red/Orange, 수정 / 수정 불필요 / 확인 필요), 검사 종류별·파일별 표, 행마다 한 줄짜리 표. "보기"를 누르면 그 행의 diff로 갑니다.
    - "자세히 보기 (diff)": 행마다 판단 근거와 diff
    - "확인 필요"는 LLM이 처리하지 못했거나 소스를 찾지 못한 행입니다. 무엇이 문제인지와 할 일을 두 줄로 보여주고, 개발자용 내용과 대화 기록 파일 이름은 "자세히 (개발자용)"을 펼치면 보입니다.
 4. 작업이 끝나면 적용할 수정에 체크하거나 "수정 모두 선택"을 누른 뒤 "패치 내려받기"를 누릅니다.
-   - 서로 다른 수정이 같은 줄을 고치면 함께 적용할 수 없습니다. "수정 모두 선택"은 Red를 먼저, 그다음 엑셀 행 순서로 겹치지 않는 것만 고릅니다.
+   - 서로 다른 수정이 같은 줄을 고치면 함께 적용할 수 없습니다. "수정 모두 선택"은 중요한 대상(Red, Mandatory)을 먼저, 그다음 엑셀 행 순서로 겹치지 않는 것만 고릅니다.
    - 빠진 행에는 "이번엔 제외 · 행 N 수정과 같은 줄을 고칩니다"가 붙습니다. 직접 체크하면 겹치는 상대 행의 체크가 풀립니다.
    - 빠진 행은 패치를 적용하고 Polyspace를 다시 돌린 뒤, 새 엑셀로 다시 분석하면 됩니다. 이미 고쳐졌으면 다음 결과에 나오지 않습니다.
 5. 화면에 나온 폴더에서 패치를 확인한 뒤 적용합니다. `core.autocrlf=false`는 Git for Windows가 LF 파일을 CRLF로 바꾸지 않게 합니다.
@@ -92,7 +96,7 @@ git -c core.autocrlf=false apply fixes.patch
 
 - 소스는 UTF-8 또는 CP949여야 하고, 한 파일 안의 줄바꿈은 한 가지(CRLF 또는 LF)여야 합니다.
 - 작업과 결과는 `private/fixer.db`에 저장되며 커밋되지 않습니다. 같은 파일·같은 지적은 LLM을 다시 부르지 않습니다.
-- LLM과 오간 대화는 행마다 `private/llm-logs/시각_job작업번호_row엑셀행.md`로 남습니다. 보낸 메시지(코드 포함), 받은 응답, 응답 원문 JSON이 그대로 들어 있고 API 키는 없습니다. 이전 결과를 재사용해 LLM을 부르지 않은 행도 그렇다고 적힌 파일이 남습니다.
+- LLM과 오간 대화는 묶음마다 `private/llm-logs/시각_job작업번호_row엑셀행.md`로 남습니다(엑셀 행은 묶음의 가장 작은 행). 보낸 메시지(코드 포함), 받은 응답, 응답 원문 JSON이 그대로 들어 있고 API 키는 없습니다. 이전 결과를 재사용해 LLM을 부르지 않은 행도 그렇다고 적힌 파일이 남습니다.
 - 서비스를 껐다 켜면 처리 중이던 작업을 이어서 합니다.
 - 주소: 새 분석 `/`, 작업 목록 `/results`, 작업 결과 `/results/{작업 번호}`, 결과 JSON `/api/results/{작업 번호}`.
 
diff --git a/docs/superpowers/specs/2026-10-07-rte-fixer-design.md b/docs/superpowers/specs/2026-10-07-rte-fixer-design.md
index 6d19cf4..8cf1468 100644
--- a/docs/superpowers/specs/2026-10-07-rte-fixer-design.md
+++ b/docs/superpowers/specs/2026-10-07-rte-fixer-design.md
@@ -2,6 +2,7 @@
 
 - 작성일: 2026-10-07
 - 상태: 승인됨 (구현 계획: `docs/superpowers/plans/2026-10-07-rte-fixer.md`)
+- 이후 변경: 시트 종류 기반과 MISRA 지원(`2026-10-08-sheet-kinds-misra-design.md`)으로 엑셀 읽기, LLM 답 형식, 캐시 키, DB(스키마 3), 작업자, 화면 일부가 바뀌었다. 겹치는 내용은 그 문서가 우선이다.
 
 ## 1. 배경과 목표
 
````

- [ ] **Step 2: todo.md에 회사 PC 확인 절 더하기**

`git fetch origin`으로 최신 `todo.md`를 받은 뒤(다른 작업도 이 파일을 고친다), 비어 있는 다음 번호로 아래 절을 더한다. "순서 요약" 표가 있으면 그 번호의 줄도 더한다(할 일: "MISRA 분석해 보기", 걸리는 시간: "엑셀 크기와 Polyspace 실행 시간에 따라 다름"). 사내 정보 규칙(설계 10절)을 지킨다.

````markdown
## N. MISRA 분석해 보기

MISRA 시트 분석, 같은 줄 묶음, 조건 시트 읽기를 처음 써 보는 절입니다. 결과 화면은 회사 PC 안에서만 보고, 아래 "알려줄 것"은 개수와 종류만 말로 알려 주세요. 파일·함수 이름, 코드, 경로는 적지 않습니다.

1. 최신 코드를 받습니다.

```bash
git pull
```

2. 서버를 켭니다.

```bash
uv run llm-fix-server
```

3. 첫 화면에서 엑셀과 소스 폴더를 고르고, "분석할 시트"에서 MISRA C:2012를 고른 뒤 Mandatory만 체크하고 "분석 시작"을 누릅니다.
4. 끝나면 결과를 훑어보고 "수정 모두 선택", "패치 내려받기"를 차례로 누른 뒤, 화면의 "패치 적용 방법"대로 적용합니다. 처음에는 원본 폴더를 따로 복사해 두고 해 보면 안전합니다.
5. Polyspace를 다시 돌립니다.

알려줄 것:
- 결과 화면 맨 위 숫자: 수정 / 수정 불필요 / 확인 필요 개수
- 작업 메모 줄에 "조건 시트: 규칙 N개"가 나왔는지("조건 시트 없음"이면 그렇다고), 분류가 다르다는 메모("분류: 조건 시트 …")가 붙은 행이 몇 개였는지
- "함께 보낸 행"이 붙은 행이 있었는지
- `git apply --check`와 `git apply`가 됐는지, 안 됐다면 오류의 종류
- Polyspace 재실행 뒤 "수정" 행의 위반이 없어졌는지, 새 MISRA 위반이 몇 개 생겼는지
- 이상했던 판단이 있었다면 규칙 번호와 함께, 코드와 이름 없이 말로
````

- [ ] **Step 3: 전체 확인**

Run: `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`
Expected: `372 passed`, 린트 통과

- [ ] **Step 4: 커밋과 push**

```bash
git add README.md docs/superpowers/specs/2026-10-07-rte-fixer-design.md todo.md
```

```bash
git commit -m "docs: describe MISRA and sheet kinds in the README" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

```bash
git fetch origin
```

```bash
git rebase origin/master
```

```bash
git push origin HEAD:master
```

- [ ] **Step 5: 다른 작업에 알리기**

기반이 master에 올라갔다고 "Codemetrics 준비"와 "MISRA/코드 메트릭/RTE 조건 통합" 세션에 알린다. 커밋 번호와, 설계 3.2·6.7·13절에 기반의 자리가 정리되어 있다는 것을 함께 적는다. 회사 값은 넣지 않는다.
