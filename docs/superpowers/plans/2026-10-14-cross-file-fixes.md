# 다른 파일까지 고치는 RTE 수정안 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 소스 폴더에 프로젝트 전체를 고르게 하고(참고 폴더 없앰), RTE 요청에 붙인 정의를 LLM이 고칠 수 있게 해서 그 수정이 결과 diff와 패치에 함께 들어가게 한다.

**Architecture:** `Edit`에 `file`(빈 글이면 행의 파일)을 더하고, 충돌 검사·패치가 수정마다 실제 파일을 본다. RTE 요청은 작업에 저장된 파일 원문이 있는 정의를 "editable"로 번호와 함께 보내고, 답의 `edits[].file`에 따라 그 파일의 보여 준 줄 범위에서 수정을 찾는다. 작업을 만들 때 정의가 든 파일 원문을 함께 저장하고, 결과 화면은 다른 파일 수정 행을 자동 선택에서 빼고 칩을 붙인다.

**Tech Stack:** Python 3.14, uv, pytest, ruff, FastAPI, SQLite. 의존성 추가 없음.

**Spec:** `docs/superpowers/specs/2026-10-14-cross-file-fixes-design.md`

## Global Constraints

- 코드·테스트·문서·커밋에 회사 값(파일·함수·매크로 이름, 개수, 임계값)을 쓰지 않는다. 테스트 이름은 지어낸다.
- 커밋 전에 `uv run ruff format .`, `uv run ruff check .`, `uv run pytest -q`가 모두 통과해야 한다. 줄 길이 100칸, 한글은 2칸으로 센다.
- 커밋 메시지는 영어, 끝 줄은 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 파일의 원래 줄바꿈(CRLF/LF)을 지킨다. 파일 전체가 바뀐 diff는 줄바꿈을 바꾼 것이다.
- 다른 파일 수정은 RTE에서만(`Kind.edits_definitions`). MISRA·CodeMetrics 요청과 답은 바뀌지 않는다.
- `Edit.file`은 빈 글이면 행의 파일(검사 파일)이다. 다른 파일이면 작업 파일 이름(소스 폴더 기준 상대 경로, `/` 구분)이다.
- 다른 파일 수정이 있는 행은 "수정 모두 선택"이 고르지 않는다. 칩 글은 `다른 파일 수정`.
- 정의 하나에서 고칠 수 있는 줄은 보여 준 줄까지다: `min(last, first + MAX_DEFINITION_LINES - 1)`.
- 예전 결과(`file` 없음)와 예전 작업(정의 파일 원문 없음)은 지금처럼 동작한다.
- 모든 명령은 저장소 루트에서 `uv run …`.

## Review Focus

- 같은 함수를 두 묶음이 똑같이 고친 경우(한쪽은 그 함수가 든 파일이 검사 파일이라 `file=""`, 다른 쪽은 `file="lib/x.c"`): 충돌이 아니라 하나로 합쳐져야 한다. (Task 2 테스트)
- 검사 파일 자신의 다른 함수(보낸 범위 밖)에 있는 정의를 고치는 답: `file`이 검사 파일 이름이어도 받아들이고, 저장할 때는 `file=""`로 정규화한다. (Task 3 테스트)
- 80줄에서 잘린 정의의 잘린 뒷부분을 고치는 답: 범위 밖이므로 다시 묻는다. (Task 3 테스트)
- 예전 작업(정의 파일 원문 없음)을 이어서 처리: 정의는 참고만으로 가고 요청이 깨지지 않는다. (Task 3 테스트)
- 프로젝트 전체를 고른 작업의 패치 경로가 프로젝트 기준(`app/src/calc.c`, `common/drv.c`)이고 `git apply`가 통과한다. (Task 5 테스트)

---

### Task 1: 참고 폴더 없애고 프로젝트 전체를 고르게 하기

**Files:**
- Modify: `src/llm_lab/web.py` (`_job_note`, `_reference_prefix`, `create_job` 처리기, `import os`)
- Modify: `src/llm_lab/pages.py` (`index_page`의 2단계)
- Modify: `src/llm_lab/csource.py` (`scan_folder`)
- Modify: `README.md`, `todo.md`
- Test: `tests/test_web.py`, `tests/test_csource.py`

**Interfaces:**
- Produces: `scan_folder(root: Path) -> tuple[list[Definition], int]` (skip·prefix 매개변수 없음). `_job_note(sheet, definitions=0, unreadable=0, mode_label="")`. 테스트 도우미 `upload(client, excel, folder, kind="rte", choices=(...), mode=None)` (`ref_dirs` 없음). 테스트 도우미 `project_with_shared_code(tmp_path) -> tuple[Path, Path]` (app, top)은 그대로 둔다.

- [ ] **Step 1: 테스트 고치기 (실패하게)**

`tests/test_csource.py`에서 `test_scan_folder_can_prefix_names_and_skip_the_source_folder`를 지운다.

`tests/test_web.py`에서:
- `upload`의 `ref_dirs=None` 매개변수와 `if ref_dirs:` 두 줄을 지운다.
- `test_missing_reference_folder_is_rejected`, `test_home_has_a_reference_folder_box`를 지운다.
- `test_reference_folders_supply_definitions_outside_the_source_folder`를 아래로 바꾼다.

```python
def test_project_folder_supplies_definitions_from_sibling_folders(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))
    app, top = project_with_shared_code(tmp_path)
    excel = rte_excel(rte_row("Orange Check", line=4))

    response = upload(client, excel, top)

    assert response.status_code == 303
    content = sent[0]["messages"][1]["content"]
    assert "#define api_scale(v) drv_scale(v)" in content
    assert "int drv_scale(int v)" in content
    assert "common/drv.c" in content and "../" not in content
    assert store.rows(1)[0].file_name == "app/src/calc.c"
    assert "참고 폴더" not in store.job(1).note


def test_home_asks_for_the_whole_project_folder(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="ref_dirs"' not in page
    assert "프로젝트 최상위 폴더" in page
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_web.py -k "project_folder or whole_project"`
Expected: `test_home_asks_for_the_whole_project_folder` FAIL (`name="ref_dirs"`가 아직 있음)

- [ ] **Step 3: 구현**

`src/llm_lab/web.py`:
- `_job_note`에서 `references: int = 0` 매개변수와 `if references: parts.append(f"참고 폴더 {references}개")` 두 줄을 지운다.
- `_reference_prefix` 함수를 통째로 지운다. `os`를 다른 곳에서 쓰지 않으면 `import os`도 지운다.
- `create_job`의 `ref_dirs: Annotated[str, Form()] = "",` 매개변수, 참고 폴더 검사 블록(`# 참고 폴더:` 주석부터 `return HTMLResponse(error_page("참고 폴더를 찾을 수 없음", ...` 까지), 정의 스캔의 `for folder in references:` 반복 전체를 지운다.
- `note=_job_note(...)` 호출에서 `len(references),` 인자를 지운다.

`src/llm_lab/csource.py`의 `scan_folder`를 원래 모양으로 되돌린다.

```python
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
```

`src/llm_lab/pages.py`의 `index_page` 2단계:
- `<h3>소스 폴더</h3>`를 `<h3>소스 폴더 (프로젝트 전체)</h3>`로 바꾼다.
- 그 아래 안내 `<p class="hint">`의 글을 다음으로 바꾼다: `부르는 함수와 헤더까지 모두 들어 있는 프로젝트 최상위 폴더. "폴더 찾기…"로 고르거나 경로를 붙여넣습니다. 엑셀에 나온 파일을 이 폴더 안에서 찾고, 패치도 이 폴더 기준으로 만듭니다.`
- 참고 폴더 안내 `<p class="hint">참고 폴더 (선택): …</p>`와 `<textarea name="ref_dirs" …></textarea>`를 지운다.

`README.md`: "참고 폴더"를 설명하는 문장을 지우고, 같은 자리에 `소스 폴더는 부르는 함수와 헤더까지 모두 들어 있는 프로젝트 최상위 폴더를 고릅니다. 매크로가 다른 함수 이름을 가리키기만 하면(`#define A B`, `#define A(x) B(x)`) 그 함수의 구현도 따라가 붙입니다(3단계까지).`를 둔다. `grep -n "참고 폴더" README.md`가 아무것도 내지 않아야 한다.

`todo.md` 2절:
- 5번 단계의 `③ 부르는 함수나 헤더가 있는 상위·옆 폴더를 "참고 폴더"에 한 줄씩 적고 ④ 처리 방식은`을 `③ 처리 방식은`으로 바꾸고, `② 2의 소스 폴더`를 `② 2의 소스 폴더 대신 프로젝트 최상위 폴더`로 바꾼다.
- 알려줄 것의 `"참고 폴더 N개", `를 지운다.
`grep -n "참고 폴더" todo.md`가 아무것도 내지 않아야 한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_web.py tests/test_csource.py tests/test_pages.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add -A src tests README.md todo.md
git commit -m "feat: pick the whole project as the source folder; reference folders go away"
```

---

### Task 2: 수정마다 파일을 갖고, 충돌 검사와 패치가 그 파일을 본다

**Files:**
- Modify: `src/llm_lab/fixer.py` (`Edit`)
- Modify: `src/llm_lab/patch.py` (`find_conflicts`)
- Modify: `src/llm_lab/web.py` (`download_patch`)
- Test: `tests/test_patch.py`, `tests/test_web.py`

**Interfaces:**
- Produces: `Edit(start, end, replacement, file="")`. `find_conflicts(rows: Mapping[int, tuple[str, Sequence[Edit]]])`는 그대로 쓰되 수정마다 `edit.file or 행의 파일`로 비교한다. `download_patch`는 수정을 실제 파일(`edit.file or row.file_name`)별로 모은다.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_patch.py` 끝에 더한다.

```python
def test_edits_in_other_files_conflict_only_in_the_same_file():
    a = Edit(10, 20, "x", file="lib/drv.c")
    b = Edit(15, 25, "y", file="lib/drv.c")
    c = Edit(15, 25, "y", file="lib/other.c")

    assert find_conflicts({1: ("calc.c", [a]), 2: ("main.c", [b])}) == {1: {2}, 2: {1}}
    assert find_conflicts({1: ("calc.c", [a]), 2: ("main.c", [c])}) == {1: set(), 2: set()}
    # 행의 파일로 정해지는 수정과 file이 적힌 수정도 같은 파일이면 비교한다
    d = Edit(15, 25, "y")
    assert find_conflicts({1: ("calc.c", [a]), 2: ("lib/drv.c", [d])}) == {1: {2}, 2: {1}}


def test_same_change_written_with_and_without_file_is_not_a_conflict():
    named = Edit(10, 20, "x", file="lib/drv.c")
    local = Edit(10, 20, "x")

    assert find_conflicts({1: ("calc.c", [named]), 2: ("lib/drv.c", [local])}) == {
        1: set(),
        2: set(),
    }


def test_old_results_without_file_still_load():
    result = FixResult.from_dict(
        {
            "decision": "fix",
            "reason": "r",
            "edits": [{"start": 1, "end": 2, "replacement": "z"}],
            "diff": "",
        }
    )

    assert result.edits == (Edit(1, 2, "z"),)
    assert result.edits[0].file == ""
```

`tests/test_patch.py` 위쪽 import에 `FixResult`(`llm_lab.fixer`)와 `find_conflicts`(`llm_lab.patch`)가 없으면 더한다.

`tests/test_web.py` 끝에 더한다(import에 `from llm_lab.fixer import Edit, FixResult`는 이미 있다).

```python
DRV_C = b"int drv_scale(int v)\n{\n    return v + 1;\n}\n"


def test_patch_carries_edits_in_other_files(tmp_path):
    client, store, _ = make_service(tmp_path)
    calc = decode_source("src/calc.c", CALC_C)
    drv = decode_source("lib/drv.c", DRV_C)
    job_id = store.create_job(
        "rte.xlsx",
        "RTE_Result",
        0,
        [calc, drv],
        [NewRow(finding(), "src/calc.c", "pending")],
        source_root=str(tmp_path),
    )
    (row,) = store.rows(job_id)
    local = Edit(CALC_C.index(b"a / b"), CALC_C.index(b"a / b") + 5, "a / (b ? b : 1)")
    start = DRV_C.index(b"v + 1")
    other = Edit(start, start + 5, "v + 2", file="lib/drv.c")
    store.set_row(row.id, "done", FixResult("fix", "r", (local, other), "diff"))

    response = client.post(f"/results/{job_id}/fixes.patch", data={"row": [str(row.id)]})

    assert response.status_code == 200
    assert b"--- a/lib/drv.c\n+++ b/lib/drv.c\n" in response.content
    assert b"+    return v + 2;\n" in response.content
    assert b"--- a/src/calc.c\n+++ b/src/calc.c\n" in response.content
```

`decode_source`, `NewRow`, `finding`, `CALC_C`가 test_web.py에 import되어 있는지 확인하고 없으면 더한다(`decode_source`는 `llm_lab.sources`, `NewRow`는 `llm_lab.store`, `finding`·`CALC_C`는 `rte_fixtures`).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_patch.py tests/test_web.py -k "other_file or without_file or other_files"`
Expected: `Edit.__init__() got an unexpected keyword argument 'file'`로 FAIL

- [ ] **Step 3: 구현**

`src/llm_lab/fixer.py`의 `Edit`:

```python
@dataclass(frozen=True)
class Edit:
    start: int  # 원본 텍스트(\n 통일) 안의 문자 위치
    end: int
    replacement: str
    # 고치는 파일. 빈 글이면 행의 파일(검사 파일), 아니면 작업 파일 이름(소스 폴더 기준 경로)
    file: str = ""
```

`FixResult.from_dict`는 `Edit(**edit)`이라 `file`이 없는 예전 결과도 그대로 읽힌다(고칠 것 없음).

`src/llm_lab/patch.py`의 `find_conflicts`:

```python
def find_conflicts(rows: Mapping[int, tuple[str, Sequence[Edit]]]) -> dict[int, set[int]]:
    """행 → 함께 고를 수 없는 행들. rows는 행 → (행의 파일, 그 행의 수정들).

    수정마다 실제 파일(edit.file, 비었으면 행의 파일)을 보고 같은 파일에서 겹칠 때만 충돌이다.
    """
    resolved = {
        row: [(edit.file or file, replace(edit, file="")) for edit in edits]
        for row, (file, edits) in rows.items()
    }
    conflicts: dict[int, set[int]] = {row: set() for row in rows}
    items = list(resolved.items())
    for index, (row_a, edits_a) in enumerate(items):
        for row_b, edits_b in items[index + 1 :]:
            if any(
                file_a == file_b and _conflict(a, b)
                for file_a, a in edits_a
                for file_b, b in edits_b
            ):
                conflicts[row_a].add(row_b)
                conflicts[row_b].add(row_a)
    return conflicts
```

`patch.py` 위에 `from dataclasses import dataclass, replace`로 `replace`를 더한다.

`build_patch`는 그대로 둔다(파일별 `FilePatch` 안에서는 `file`을 비운 수정만 받는다. 아래 `download_patch`가 비운다).

`src/llm_lab/web.py`의 `download_patch`에서 `by_file`을 채우는 반복을 바꾼다.

```python
by_file: dict[str, FilePatch] = {}
for r in chosen:
    assert r.file_name is not None and r.result is not None
    for edit in r.result.edits:
        target = edit.file or r.file_name
        if target not in by_file:
            source = store.source(job_id, target)
            # 폴더에서 읽은 소스는 이름이 곧 폴더 기준 상대 경로다
            path = source.name if job.source_root else relative_path(r.finding.file, base)
            by_file[target] = FilePatch(path, source, [])
        by_file[target].edits.append((r.finding.row, replace(edit, file="")))
```

`web.py` 위에 `from dataclasses import asdict, replace`로 `replace`를 더한다(이미 `asdict`를 가져온다).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_patch.py tests/test_web.py tests/test_pages.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/fixer.py src/llm_lab/patch.py src/llm_lab/web.py tests/test_patch.py tests/test_web.py
git commit -m "feat: an edit names its file; conflicts and patches follow each edit's file"
```

---

### Task 3: RTE 요청에 고칠 수 있는 정의를 보내고, 답의 다른 파일 수정을 찾는다

**Files:**
- Modify: `src/llm_lab/kinds/base.py` (`Kind.edits_definitions`)
- Modify: `src/llm_lab/fixer.py` (`FIX_SCHEMA_WITH_FILE`, `Request.files`, `definitions_block`, `build_prompt`, `make_request`, `cache_key`, `_located_edits`)
- Modify: `src/llm_lab/kinds/rte.py` (`edits_definitions=True`, `answer_schema=FIX_SCHEMA_WITH_FILE`)
- Test: `tests/test_fixer.py`

**Interfaces:**
- Consumes: `Edit.file` (Task 2), `csource.MAX_DEFINITION_LINES`, `csource.Definition`
- Produces: `make_request(kind, findings, source, context=None, definitions=(), files=None)` — `files: Mapping[str, SourceFile] | None`는 작업에 저장된 파일(이름 → 원문). `Request.files: Mapping[str, SourceFile]`는 이번 요청에서 고칠 수 있게 보낸 정의의 파일만. `Kind.edits_definitions: bool = False`. `fixer.FIX_SCHEMA_WITH_FILE`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_fixer.py` 끝에 더한다. import에 `from llm_lab.csource import MAX_DEFINITION_LINES, Definition`(Definition이 이미 있으면 MAX만), `from llm_lab.fixer import FIX_SCHEMA_WITH_FILE`을 더한다.

```python
USE_DRV = decode_source(
    "src/calc.c", b"int divide(int a, int b)\n{\n    return a / drv_scale(b);\n}\n"
)
DRV = decode_source("lib/drv.c", b"int drv_scale(int v)\n{\n    return v - 1;\n}\n")
DRV_DEF = Definition("drv_scale", "function", "lib/drv.c", 1, 4, DRV.text.rstrip("\n"))
DRV_FIX = {"file": "lib/drv.c", "original": "    return v - 1;", "replacement": "    return v + 1;"}


def ask_with(handler, *, definitions=(DRV_DEF,), files=None, kind=RTE, source=USE_DRV):
    files = {"lib/drv.c": DRV} if files is None else files
    request = make_request(kind, [finding(line=3)], source, None, definitions, files)
    return request, propose_fixes(make_client(handler), MODEL, request)


def test_rte_sends_definitions_with_stored_files_as_editable_numbered_code():
    request = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV})

    content = request.messages[1]["content"]
    assert "Those marked editable may be changed" in content
    assert "// editable: lib/drv.c lines 1-4\n    1| int drv_scale(int v)" in content
    assert set(request.files) == {"lib/drv.c"}
    assert RTE.answer_schema is FIX_SCHEMA_WITH_FILE
    assert FIX_SCHEMA_WITH_FILE["properties"]["edits"]["items"]["required"] == [
        "file",
        "original",
        "replacement",
    ]


def test_definitions_without_a_stored_file_stay_reference_only():
    # 예전 작업: 정의 파일 원문이 작업에 없다
    request = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {})

    content = request.messages[1]["content"]
    assert "// reference only: lib/drv.c lines 1-4\nint drv_scale(int v)" in content
    assert "editable:" not in content and request.files == {}


def test_misra_never_marks_definitions_editable():
    request = make_request(
        MISRA, [misra_finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV}
    )

    content = request.messages[1]["content"]
    assert "editable" not in content
    assert "for reference only" in content


def test_fix_in_another_file_is_located_there_and_shown_in_the_diff():
    handler, _ = scripted(answer(("fix", REASON), edits=[DRV_FIX]))

    _, (result,) = ask_with(handler)

    (edit,) = result.edits
    assert edit.file == "lib/drv.c"
    assert apply_edits(DRV.text, [replace(edit, file="")]) == DRV.text.replace("v - 1", "v + 1")
    assert "--- a/lib/drv.c" in result.diff and "+    return v + 1;" in result.diff


def test_local_and_other_file_edits_share_one_diff_local_first():
    local = {
        "file": "",
        "original": "    return a / drv_scale(b);",
        "replacement": "    return a / (drv_scale(b) ? drv_scale(b) : 1);",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[DRV_FIX, local]))

    _, (result,) = ask_with(handler)

    assert [edit.file for edit in result.edits] == ["", "lib/drv.c"]
    assert result.diff.index("--- a/src/calc.c") < result.diff.index("--- a/lib/drv.c")


def test_edit_in_the_checked_file_outside_the_sent_range_uses_its_definition():
    # 검사 파일 안의 다른 함수(보낸 범위 밖)도 정의로 붙었으면 고칠 수 있고, file은 비운다
    text = (
        "int helper(int v)\n{\n    return v - 1;\n}\n"
        + "\n" * 2100
        + "int divide(int a, int b)\n{\n    return a / helper(b);\n}\n"
    )
    source = decode_source("src/calc.c", text.encode())
    helper = Definition(
        "helper", "function", "src/calc.c", 1, 4, "int helper(int v)\n{\n    return v - 1;\n}"
    )
    fix = {
        "file": "src/calc.c",
        "original": "    return v - 1;",
        "replacement": "    return v + 1;",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[fix]))
    line = text.count("\n", 0, text.index("return a / helper")) + 1

    request = make_request(
        RTE, [finding(line=line)], source, None, [helper], {"src/calc.c": source}
    )
    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.edits[0].file == "" and result.edits[0].start == text.index("    return v - 1;")


@pytest.mark.parametrize(
    "bad",
    [
        {"file": "lib/unknown.c", "original": "    return v - 1;", "replacement": "x"},
        {"file": "lib/drv.c", "original": "    return a / drv_scale(b);", "replacement": "x"},
    ],
)
def test_other_file_edit_outside_what_was_shown_is_asked_again(bad):
    handler, sent = scripted(
        answer(("fix", REASON), edits=[bad]), answer(("fix", REASON), edits=[DRV_FIX])
    )

    _, (result,) = ask_with(handler)

    assert len(sent) == 2
    assert result.edits[0].file == "lib/drv.c"


def test_clipped_definition_can_be_edited_only_in_the_shown_lines():
    body = "\n".join(f"    v += {i};" for i in range(MAX_DEFINITION_LINES + 10))
    text = f"int drv_scale(int v)\n{{\n{body}\n    return v;\n}}\n"
    drv = decode_source("lib/drv.c", text.encode())
    lines = text.split("\n")
    clipped = Definition(
        "drv_scale",
        "function",
        "lib/drv.c",
        1,
        len(lines) - 1,
        "\n".join(lines[:MAX_DEFINITION_LINES]) + "\n... (more)",
    )
    hidden = {"file": "lib/drv.c", "original": "    return v;", "replacement": "    return v + 1;"}
    shown = {"file": "lib/drv.c", "original": "    v += 0;", "replacement": "    v += 100;"}
    handler, sent = scripted(
        answer(("fix", REASON), edits=[hidden]), answer(("fix", REASON), edits=[shown])
    )

    _, (result,) = ask_with(handler, definitions=(clipped,), files={"lib/drv.c": drv})

    assert len(sent) == 2
    assert "v += 100;" in result.diff


def test_editable_file_contents_are_part_of_the_cache_key():
    changed = decode_source("lib/drv.c", DRV.text.encode() + b"int other;\n")
    a = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV})
    b = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": changed})

    assert cache_key(MODEL, a) != cache_key(MODEL, b)
```

`replace`는 `dataclasses.replace`로 이미 import되어 있다. `misra_finding`도 이미 import되어 있다. `finding(line=3)`이 `misra_finding(line=3)` 꼴로 안 되면 `misra_finding()`의 기본 줄로 바꾸되, `USE_DRV`가 4줄이라 1~4 안이어야 한다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_fixer.py -k "editable or reference_only or never_marks or another_file or share_one_diff or outside or clipped or cache_key"`
Expected: `ImportError: cannot import name 'FIX_SCHEMA_WITH_FILE'`

- [ ] **Step 3: 구현**

`src/llm_lab/kinds/base.py`의 `Kind`에서 `uses_definitions` 줄 아래:

```python
    # 붙인 정의 가운데 작업에 원문이 있는 것을 LLM이 고칠 수 있는지 (답의 edits[].file)
    edits_definitions: bool = False
```

`src/llm_lab/fixer.py`:

1) `FIX_SCHEMA` 바로 아래:

```python
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
```

위에 `import copy`, 그리고 `from collections.abc import Iterable, Mapping`(Iterable은 이미 있다), `from dataclasses import asdict, dataclass, field`, `from llm_lab.csource import MAX_DEFINITION_LINES, ...`(기존 csource import 줄에 더한다).

2) `Request`의 마지막 필드 뒤:

```python
    # 고칠 수 있게 보낸 정의의 파일 (이름 → 원문). 비었으면 정의는 참고만이다
    files: Mapping[str, SourceFile] = field(default_factory=dict)
```

3) `definitions_block`을 바꾼다.

```python
def _shown_last(definition: Definition) -> int:
    """정의에서 보여 주는(고칠 수 있는) 마지막 줄."""
    return min(definition.last, definition.first + MAX_DEFINITION_LINES - 1)


def definitions_block(
    definitions: tuple[Definition, ...], editable: Mapping[str, SourceFile] | None = None
) -> str:
    """코드 뒤에 붙이는 다른 곳의 정의 절.

    editable에 원문이 있는 파일의 정의는 줄 번호를 붙여 고칠 수 있게 보내고, 나머지는 번호 없이
    참고로만 보낸다. editable이 비었으면 예전처럼 모두 참고만이다.
    """
    if not definitions:
        return ""
    editable = editable or {}
    parts = []
    for definition in definitions:
        where = (
            f"line {definition.first}"
            if definition.first == definition.last
            else f"lines {definition.first}-{definition.last}"
        )
        source = editable.get(definition.file)
        if source is None:
            label = "reference only: " if editable else ""
            parts.append(f"```c\n// {label}{definition.file} {where}\n{definition.text}\n```")
            continue
        last = _shown_last(definition)
        body = numbered(source.text, definition.first, last)
        if last < definition.last:
            body += f"\n... ({definition.last - last} more lines, not editable)"
        parts.append(f"```c\n// editable: {definition.file} {where}\n{body}\n```")
    if not editable:
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
```

4) `build_prompt`에 `editable: Mapping[str, SourceFile] | None = None` 매개변수를 끝에 더하고, `definitions_block(definitions)`를 `definitions_block(definitions, editable)`로 바꾼다.

5) `make_request`의 서명 끝에 `files: Mapping[str, SourceFile] | None = None`을 더하고, `picked`를 정한 뒤:

```python
editable: dict[str, SourceFile] = {}
if kind.edits_definitions and files:
    editable = {d.file: files[d.file] for d in picked if d.file in files}
messages = [
    {"role": "system", "content": kind.prompt(context)},
    {
        "role": "user",
        "content": build_prompt(kind, findings, source, first, last, context, picked, editable),
    },
]
return Request(kind, list(findings), source, first, last, context, messages, picked, editable)
```

6) `cache_key`의 `parts` 끝에 `*(f"{name}:{f.sha256}" for name, f in sorted(request.files.items())),`를 더한다(파일이 없으면 키가 예전과 같다).

7) `_located_edits`를 바꾼다.

```python
def _ranges(request: Request, target: str) -> list[tuple[int, int]]:
    """target 파일에서 수정을 찾을 줄 범위들: 검사 파일은 보낸 범위, 그리고 고칠 수 있게 보낸 정의."""
    ranges = [(request.first, request.last)] if target == request.source.name else []
    if target in request.files:
        ranges += [(d.first, _shown_last(d)) for d in request.definitions if d.file == target]
    return ranges


def _locate_one(
    text: str,
    ranges: list[tuple[int, int]],
    raw: dict[str, str],
    line: int | None,
    number: int,
    name: str,
) -> tuple[Edit, ...]:
    """수정 하나를 범위들 가운데 정확히 한 곳에서 찾는다."""
    found: list[tuple[Edit, ...]] = []
    for first, last in ranges:
        try:
            found.append(locate_edits(text, first, last, [raw], line))
        except EditMismatch:
            continue
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    where = "찾을 수 없음" if not unique else "여러 곳에 나옴"
    raise EditMismatch(f"edits[{number}].original을 {name}의 보여 준 코드에서 {where}")


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
    texts = {source.name: source, **request.files}
    by_target: dict[str, list[Edit]] = {}
    for number, raw in enumerate(raw_edits, start=1):
        target = raw.get("file") or source.name
        if target not in texts:
            raise EditMismatch(f"edits[{number}].file {target}은 고칠 수 있게 보낸 파일이 아님")
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
        found = sorted(by_target.get(target, []), key=lambda edit: edit.start)
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
```

`fixer.py` 위 import에 `from dataclasses import asdict, dataclass, field, replace`로 `replace`도 더한다.

`src/llm_lab/kinds/rte.py`의 `RTE = Kind(` 인자에 `edits_definitions=True,`와 `answer_schema=FIX_SCHEMA_WITH_FILE,`를 더하고, 위 import에 `from llm_lab.fixer import FIX_SCHEMA_WITH_FILE, select_region`(기존 `select_region` import 줄에 더한다).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_fixer.py tests/test_fixer_edits.py tests/test_kinds.py tests/test_worker.py`
Expected: PASS. `test_fix_returns_edits_diff_and_sends_schema_and_numbered_code`의 `schema["required"] == ["findings", "edits"]`는 바깥 required라 그대로 맞는다.

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/fixer.py src/llm_lab/kinds/base.py src/llm_lab/kinds/rte.py tests/test_fixer.py
git commit -m "feat: RTE requests mark stored definitions editable and accept edits in their files"
```

---

### Task 4: 작업이 정의 파일 원문을 저장하고 작업자가 넘긴다

**Files:**
- Modify: `src/llm_lab/store.py` (`Store.sources`)
- Modify: `src/llm_lab/web.py` (`create_job` 처리기)
- Modify: `src/llm_lab/worker.py` (`_process`)
- Test: `tests/test_store.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `make_request(..., definitions, files)` (Task 3), `Kind.edits_definitions`
- Produces: `Store.sources(job_id: int) -> dict[str, SourceFile]`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_store.py` 끝:

```python
def test_sources_returns_every_file_of_a_job(tmp_path):
    store = Store(tmp_path / "fixer.db")
    other = decode_source("lib/drv.c", b"int drv;\n")
    job_id = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [SOURCE, other], [NewRow(FINDING, "calc.c", "pending")]
    )

    assert set(store.sources(job_id)) == {"calc.c", "lib/drv.c"}
    assert store.sources(job_id)["lib/drv.c"] == other
    assert store.sources(job_id + 1) == {}
```

`tests/test_web.py` 끝:

```python
def test_rte_job_stores_definition_files_and_sends_them_editable(tmp_path):
    drv_fix = {
        "file": "common/drv.c",
        "original": "    return v + 1;",
        "replacement": "    return v + 2;",
    }
    client, store, sent = make_service(tmp_path, answer(("fix", REASON), edits=[drv_fix]))
    app, top = project_with_shared_code(tmp_path)

    upload(client, rte_excel(rte_row("Orange Check", line=4)), top)

    assert "common/drv.c" in store.sources(1)
    content = sent[0]["messages"][1]["content"]
    assert "// editable: common/drv.c lines 1-4\n    1| int drv_scale(int v)" in content
    (row,) = store.rows(1)
    assert row.result.decision == "fix"
    assert [edit.file for edit in row.result.edits] == ["common/drv.c"]


def test_misra_job_does_not_store_extra_files(tmp_path):
    client, store, _ = make_service(tmp_path, fix_answer("no_fix"))
    app, top = project_with_shared_code(tmp_path)
    excel = workbook_bytes(
        {"MISRA_Result": [MISRA_HEADER, misra_row(file=r"C:\proj\app\src\calc.c", line=4)]}
    )

    upload(
        client,
        excel,
        top,
        kind="misra",
        choices=("misra:Mandatory", "misra:Required", "misra:Advisory"),
    )

    assert set(store.sources(1)) == {"app/src/calc.c"}
```

`answer`, `REASON`이 test_web.py에 import되어 있는지 확인하고 없으면 `rte_fixtures`에서 더한다. `misra_row`의 인자 이름이 다르면(`file`, `line`) 그 함수 서명에 맞춘다(`tests/misra_fixtures.py` 확인). MISRA 시트 이름이 `MISRA_Result`로 읽히지 않으면 기존 MISRA 테스트(`grep -n "MISRA_HEADER" tests/test_web.py`)가 쓰는 엑셀 만들기를 그대로 따른다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_store.py tests/test_web.py -k "sources or definition_files or extra_files"`
Expected: `AttributeError: 'Store' object has no attribute 'sources'`

- [ ] **Step 3: 구현**

`src/llm_lab/store.py`의 `source()` 메서드 뒤:

```python
    def sources(self, job_id: int) -> dict[str, SourceFile]:
        """작업에 저장한 모든 파일 (이름 → 원문)."""
        with self._db() as db:
            found = db.execute("SELECT * FROM files WHERE job_id = ?", (job_id,)).fetchall()
        return {
            record["name"]: SourceFile(
                name=record["name"],
                text=record["text"],
                encoding=record["encoding"],
                newline=record["newline"],
                bom=bool(record["bom"]),
                sha256=record["sha256"],
            )
            for record in found
        }
```

`src/llm_lab/web.py`의 `create_job` 처리기에서 `definitions = used_definitions(...)` 바로 뒤(같은 `if sheet_kind.uses_definitions:` 안):

```python
            if sheet_kind.edits_definitions:
                # 고칠 수 있게 보낼 정의가 든 파일의 원문도 작업에 저장한다(패치에 들어갈 수 있다)
                known = {source.name for source in files}
                for name in sorted({d.file for d in definitions} - known):
                    try:
                        files.append(decode_source(name, (root / name).read_bytes()))
                    except DecodeError, OSError:
                        continue
```

`web.py` 위 import: `from llm_lab.sources import DecodeError, Match, decode_source, load_from_folder`.

`src/llm_lab/worker.py`의 `_process`에서 `make_request(...)` 호출을

```python
            request = make_request(
                kind,
                findings,
                source,
                context,
                self.store.definitions(job_id),
                self.store.sources(job_id),
            )
```

로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_store.py tests/test_web.py tests/test_worker.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/store.py src/llm_lab/web.py src/llm_lab/worker.py tests/test_store.py tests/test_web.py
git commit -m "feat: RTE jobs keep the files of their definitions and the worker passes them on"
```

---

### Task 5: 결과 화면 칩, 자동 선택에서 빼기, 프로젝트 패치

**Files:**
- Modify: `src/llm_lab/pages.py` (`_selection`, `_overview_row`, `_detail_card`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Edit.file` (Task 2), Task 4의 작업 흐름

- [ ] **Step 1: 실패하는 테스트**

`tests/test_web.py` 끝(위쪽에 `from test_patch import git_apply, needs_git`를 더한다):

```python
@needs_git
def test_other_file_fix_is_not_auto_picked_shows_a_chip_and_patches_the_project(tmp_path):
    drv_fix = {
        "file": "common/drv.c",
        "original": "    return v + 1;",
        "replacement": "    return v + 2;",
    }
    client, store, _ = make_service(tmp_path, answer(("fix", REASON), edits=[drv_fix]))
    app, top = project_with_shared_code(tmp_path)
    upload(client, rte_excel(rte_row("Orange Check", line=4)), top)
    (row,) = store.rows(1)

    page = client.get("/results/1").text

    assert "다른 파일 수정" in page
    assert f'name="row" value="{row.id}" data-auto="0"' in page
    patch = client.post("/results/1/fixes.patch", data={"row": [str(row.id)]}).content
    assert b"--- a/common/drv.c\n+++ b/common/drv.c\n" in patch
    git_apply(top, patch)
    assert b"return v + 2;" in (top / "common" / "drv.c").read_bytes()
```

`tests` 폴더가 import 경로에 있어 `test_patch`를 가져올 수 있다. 안 되면 `git_apply`·`needs_git`를 `tests/test_web.py`에 그대로 옮겨 적는다(`APPLY_COMMAND`는 `llm_lab.patch`).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_web.py -k "other_file_fix"`
Expected: `assert "다른 파일 수정" in page` FAIL

- [ ] **Step 3: 구현**

`src/llm_lab/pages.py`:

1) 맨 아래 도우미 근처에 더한다.

```python
def _edits_other_files(row: Row) -> bool:
    """수정이 행의 파일 말고 다른 파일도 고치는지 (그 함수를 부르는 모든 곳에 영향)."""
    return row.result is not None and any(edit.file for edit in row.result.edits)
```

2) `_selection`의 `manual = {...}`를 바꾼다.

```python
    manual = {
        row_id
        for row_id in fixable
        if not kind.auto_select(by_id[row_id].finding) or _edits_other_files(by_id[row_id])
    }
```

3) `_overview_row`의 `elif row.result is not None:` 블록 끝, `summary_html`을 정한 뒤에 더한다.

```python
        if row.result.decision == "fix" and _edits_other_files(row):
            summary_html = f"{_badge('blue', '다른 파일 수정')} {summary_html}"
```

4) `_detail_card`의 `body.append(f'<div class="reason">…')` 바로 앞에 더한다.

```python
        if row.result.decision == "fix" and _edits_other_files(row):
            files = sorted({edit.file for edit in row.result.edits if edit.file})
            body.append(
                f'<p class="excluded">{_badge("blue", "다른 파일 수정")} '
                f"{escape(', '.join(files))}도 고칩니다. 그 함수를 부르는 모든 곳의 동작이 "
                "바뀌므로 "수정 모두 선택"이 고르지 않습니다. 보고 직접 체크하세요.</p>"
            )
```

(파이썬 문자열 안의 큰따옴표는 `\"`로 쓰거나 바깥을 작은따옴표로 바꾼다.)

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_web.py tests/test_pages.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/pages.py tests/test_web.py
git commit -m "feat: fixes that edit other files carry a chip and are left for a person to pick"
```

---

### Task 6: RTE 지시문과 문서

**Files:**
- Modify: `src/llm_lab/kinds/rte.py` (`ROOT_CAUSE_RULES`, `_FIX_RULES`, `_DEFINITIONS_GUARD`, `_DEFINITIONS_ROOT`)
- Modify: `README.md`, `todo.md`
- Test: `tests/test_kinds.py`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_kinds.py`에서:
- `test_rte_and_misra_prompts_explain_the_definitions_section`의 `assert "Edits must stay in the numbered code;" in RTE.system_prompt` 줄을 지운다(MISRA 줄은 둔다). `assert "not in the code or the definitions shown" in RTE.prompt({"mode": "root_cause"})` 줄도 지운다.
- 끝에 더한다.

```python
def test_rte_prompts_let_the_model_fix_editable_definitions():
    for mode in ("guard", "root_cause"):
        prompt = RTE.prompt({"mode": mode})
        assert "Those marked editable may be changed" in prompt
        assert 'set "file" to that path' in prompt
        assert "A change there affects every caller" in prompt
        assert 'Set "file" to "" for edits in the numbered code' in prompt
    assert "guard the operation here and name that file and function" in RTE.prompt(
        {"mode": "guard"}
    )
    root = RTE.prompt({"mode": "root_cause"})
    assert "answer manual and name that file and function" in root
    assert "the cause is in a caller or in a definition marked reference only" in root
    assert "editable" not in MISRA.system_prompt
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest -q tests/test_kinds.py -k editable_definitions`
Expected: FAIL

- [ ] **Step 3: 지시문 고치기**

`src/llm_lab/kinds/rte.py`:

`ROOT_CAUSE_RULES`의

```
- Otherwise answer manual: the cause is not in the code or the definitions shown \
(the value comes from a caller or from data that is not shown), or the only change you could make \
```

을

```
- Otherwise answer manual: the cause is in a caller or in a definition marked reference only \
(the value comes from code or data you cannot change here), or the only change you could make \
```

로 바꾼다.

`_FIX_RULES`의 마지막 단락(`Definitions of functions, macros, variables and types that this code uses but that live \`부터 `return values and the real types behind typedefs. Edits must stay in the numbered code; \`까지)을 다음으로 바꾼다.

```
Definitions of functions, macros, variables and types that this code uses but that live \
elsewhere may follow the code. Use them to judge value ranges, array sizes, \
return values and the real types behind typedefs. \
Those marked editable may be changed: when the real cause is there (for example a called \
function can return a value the caller cannot handle), fix it there and set "file" to that path. \
A change there affects every caller, so keep the behavior for valid inputs. \
Definitions marked reference only cannot be changed. \
Set "file" to "" for edits in the numbered code of the findings.
```

`_DEFINITIONS_GUARD`:

```
If the cause is in a caller or in a definition marked reference only, or changing \
the editable definition would be risky, \
guard the operation here and name that file and function in reason.
```

`_DEFINITIONS_ROOT`:

```
If the cause is in a caller or in a definition marked reference only, \
answer manual and name that file and function in reason.
```

(모두 삼중 따옴표 문자열 안이고, 줄 끝 `\`로 이어 쓰는 기존 꼴을 지킨다. `GUARD_PROMPT`·`ROOT_CAUSE_PROMPT`를 이어 붙이는 순서는 그대로다.)

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -q tests/test_kinds.py tests/test_fixer.py`
Expected: PASS

- [ ] **Step 5: 문서**

`README.md`의 RTE 처리 방식 문장 뒤에 더한다:

```
   - RTE는 붙인 정의(부르는 함수, 매크로, 전역, 타입)도 고칠 수 있습니다. 원인이 부르는 함수 쪽이면 그 파일까지 고치는 수정안이 나오고, 결과의 diff와 패치에 그 파일이 함께 들어갑니다. 그 함수를 부르는 모든 곳의 동작이 바뀌므로 이런 행에는 "다른 파일 수정" 칩이 붙고 "수정 모두 선택"이 고르지 않습니다. 보고 직접 체크합니다.
```

`todo.md` 2절 5번 단계의 `"수정 모두 선택" → "패치 내려받기"` 앞에 `"다른 파일 수정" 칩이 붙은 행은 diff를 보고 괜찮은 것만 직접 체크한 뒤`를 넣고, 알려줄 것에 한 줄을 더한다:

```
- "다른 파일 수정" 칩이 붙은 행 수, 그중 체크해 적용한 수, 적용 뒤 새로 생긴 Red·Orange 개수
```

- [ ] **Step 6: 커밋**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
git add src/llm_lab/kinds/rte.py tests/test_kinds.py README.md todo.md
git commit -m "feat: RTE prompts fix the cause in editable definitions; docs and todo follow"
```

---

## 끝낸 뒤

- 설계 문서 상태 줄에 구현 커밋을 적는다.
- origin master에 올린다(다른 세션의 커밋이 있으면 base 브랜치 동기화로 합친 뒤 검사하고 올린다).
- 현장 결과(직접 수정 수의 변화, 다른 파일 수정 행 수, 적용 뒤 새 위반)를 받으면 호출자 코드 보내기를 정한다.
