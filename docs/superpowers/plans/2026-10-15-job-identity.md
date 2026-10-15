# 작업 UUID · 이름 · 설정 기록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 작업 ID를 UUID로 바꾸고(DB·주소·기록 파일·점검 스크립트), 작업 이름과 작업 설정을 기록·표시·변경할 수 있게 한다.

**Architecture:** `Store`가 작업 키를 `uuid4` 문자열로 만들고 `title`·`settings` 칸을 둔다(스키마 6, 예전 DB는 거절). 웹·worker·기록 파일·점검 스크립트는 문자열 ID를 쓴다. 작업을 만들 때 웹이 설정 사전을 만들어 저장하고, 페이지가 그것을 보인다.

**Tech Stack:** Python 3.14, FastAPI, sqlite, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-15-job-identity-design.md`

## Global Constraints

- 작업 ID는 `str(uuid.uuid4())`(36자). 정수 작업 ID를 쓰는 곳이 남으면 안 된다(행 ID `rows.id`는 정수 그대로).
- 스키마 6. 예전 DB(user_version 1~5)를 열면 정확히 `StoreError("예전 작업 기록 형식입니다. uv run llm-fix-server --clear all 뒤 다시 켜세요.")`.
- 작업 이름 기본값 `"{엑셀 파일 이름} · {Kind.title}"`, 1~80자.
- `settings` JSON 키: `connection, model, kind, mode, choices, follow_ups, source_root, excel`. 키·프로젝트 ID는 절대 넣지 않는다.
- 캡처용 요약 페이지에는 모델·연결·작업 이름을 넣지 않는다. 제목은 `작업 요약 · {종류} · {ID 앞 8자}`.
- 점검 스크립트 화면에는 작업을 ID 앞 8자로만 가리킨다. 이름·경로·모델은 찍지 않는다.
- 사용자에게 보이는 글은 한국어. 검사: `uv run ruff format src tests scripts`, `uv run ruff check .`, `uv run pytest -q`.
- 커밋: 영어 한 줄 + 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. push하지 않는다.

## Review Focus

- 숫자 주소(`/results/1`)가 오류 페이지가 아니라 404로 끝나야 한다(예전 북마크).
- 다시 켤 때(`recover`) 처리 중이던 UUID 작업이 이어서 처리되어야 한다.
- 같은 초에 만든 두 작업의 목록 순서가 뒤집히지 않아야 한다(`rowid`로 정렬 보조).
- 예전 DB가 있을 때 `--clear all`로 지우고 다시 켤 수 있어야 한다(지우기가 Store를 열다 막히면 안 된다).
- 이름 바꾸기에 HTML이 들어 있어도 이스케이프되어야 한다.

---

### Task 1: Store를 UUID 키로 (스키마 6)

**Files:**
- Modify: `src/llm_lab/store.py`
- Modify: 정수 작업 ID를 가정하는 테스트들(`tests/test_store.py` 등 Store만 쓰는 테스트)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `Job.id: str`, `Job.title: str = ""`, `Job.settings: str = ""`(JSON, 없으면 ""), `Store.create_job(..., title: str = "", settings: Mapping[str, Any] | None = None) -> str`, `Store.rename_job(job_id: str, title: str) -> None`, 모든 `job_id` 인자 `str`. `Store.jobs()`는 `ORDER BY created_at DESC, rowid DESC`. `Store.recover() -> list[str]`. `OLD_FORMAT = "예전 작업 기록 형식입니다. uv run llm-fix-server --clear all 뒤 다시 켜세요."`.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_store.py`에 더한다:

```python
def test_jobs_get_uuid_ids_titles_and_settings(tmp_path):
    import uuid

    store = Store(tmp_path / "fixer.db")
    first = store.create_job("a.xlsx", "RTE_Result", 0, [], [], title="첫 작업",
                             settings={"model": "m", "connection": "기본 연결"})
    second = store.create_job("b.xlsx", "RTE_Result", 0, [], [])

    assert str(uuid.UUID(first)) == first
    assert [job.id for job in store.jobs()] == [second, first]
    assert store.job(first).title == "첫 작업"
    assert json.loads(store.job(first).settings) == {"model": "m", "connection": "기본 연결"}
    assert store.job(second).settings == ""
    store.rename_job(first, "새 이름")
    assert store.job(first).title == "새 이름"
    assert store.job("1") is None


def test_old_integer_database_is_refused(tmp_path):
    import sqlite3

    path = tmp_path / "fixer.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, created_at TEXT)")
        db.execute("PRAGMA user_version = 5")

    with pytest.raises(StoreError, match="예전 작업 기록 형식"):
        Store(path)
```

(`json`, `pytest`, `StoreError` import가 없으면 더한다.) 기존 테스트 가운데 예전 스키마를 옮기는 테스트(스키마 1→4, 4→5 등)는 "예전 DB는 거절"로 바꾼다. 정수 ID를 가정한 단언(`store.job(1)`, `== 1` 등)은 `create_job`이 돌려준 값을 쓰도록 고친다.

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_store.py -q`
- [ ] **Step 3: 구현**
  - SCHEMA: `jobs.id TEXT PRIMARY KEY`, `title TEXT NOT NULL DEFAULT ''`, `settings TEXT NOT NULL DEFAULT ''`; `files/rows/definitions.job_id TEXT NOT NULL`. `SCHEMA_VERSION = 6`(주석에 6: UUID 작업 키·title·settings). 예전 `ADDED_COLUMNS` 이전 경로는 지우고, 열 때 `user_version`이 1~5면 `StoreError(OLD_FORMAT)`. 새 DB(0)는 만든다. 캐시 표는 그대로.
  - `create_job`: `job_id = str(uuid.uuid4())`로 INSERT, settings는 `json.dumps(settings, ensure_ascii=False)` 또는 "".
  - `rename_job`: UPDATE.
  - 정렬은 `created_at DESC, rowid DESC`. `recent_roots`의 `MAX(id)`도 `MAX(rowid)`로.
  - 타입 표기 `job_id: int` → `str` 전부.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_store.py -q` (다른 파일의 실패는 다음 작업들에서 고친다; 이 작업의 커밋 전에 `uv run pytest -q`로 어떤 파일이 깨지는지 보고서에 적는다)
- [ ] **Step 5: 커밋** — `feat: store keys jobs by UUID with a title and recorded settings (schema 6)`

---

### Task 2: 웹·worker·기록 파일을 UUID로, 작업 만들 때 이름·설정 기록, 이름 바꾸기

**Files:**
- Modify: `src/llm_lab/web.py`, `src/llm_lab/worker.py`, `src/llm_lab/transcript.py`, `src/llm_lab/pages.py`(링크·폼 action의 ID만)
- Modify: `tests/test_web.py`, `tests/test_worker*.py`, `tests/test_codemetrics_web.py`, `tests/test_transcript.py`, `tests/test_settings_page.py` 등 정수 ID를 가정한 테스트
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1의 `Store` API.
- Produces: 라우트의 `job_id: str`(경로 파라미터). `write_log(folder, job_id: str, row, text)` 파일 이름 `{시각}_job{job_id}_row{row}.md`. `POST /results/{job_id}/rename`(폼 `title`) → 303 `/results/{job_id}`, 빈 이름·81자 이상이면 오류 페이지 400("이름은 1~80자로 적으세요."). 작업 만들 때 `title=f"{excel 파일 이름} · {sheet_kind.title}"`, `settings={"connection": 현재 연결 이름 또는 "", "model": 그 연결의 모델(연결이 없을 때는 worker.model), "kind": sheet_kind.name, "mode": 고른 처리 방식 값 또는 "", "choices": 정렬한 고른 대상 목록, "follow_ups": worker.follow_ups, "source_root": str(root), "excel": 엑셀 파일 이름}`.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web.py`에:

```python
def test_job_urls_use_uuid_and_numbers_are_404(tmp_path):
    client, store, _ = make_service(tmp_path, fix_answer("no_fix"))
    folder = source_dir(tmp_path)  # 기존 테스트가 쓰는 준비 함수 이름에 맞춘다
    upload(client, rte_excel(rte_row("Orange Check", line=3)), folder)
    (job,) = store.jobs()

    assert len(job.id) == 36
    assert client.get(f"/results/{job.id}").status_code == 200
    assert client.get("/results/1").status_code == 404
    assert job.title.endswith(" · RTE")
    settings = json.loads(job.settings)
    assert settings["kind"] == "rte" and settings["choices"] == ["Orange", "Red"]


def test_rename_job(tmp_path):
    client, store, _ = make_service(tmp_path, fix_answer("no_fix"))
    folder = source_dir(tmp_path)
    upload(client, rte_excel(rte_row("Orange Check", line=3)), folder)
    (job,) = store.jobs()

    response = client.post(f"/results/{job.id}/rename", data={"title": "<b>계산 모듈</b>"},
                           follow_redirects=False)
    page = client.get(f"/results/{job.id}").text

    assert response.status_code == 303
    assert "&lt;b&gt;계산 모듈&lt;/b&gt;" in page
    assert client.post(f"/results/{job.id}/rename", data={"title": " "}).status_code == 400
```

(`make_service`, `upload`, 소스 폴더 준비 함수는 기존 `tests/test_web.py`의 것을 쓴다. 이름이 다르면 맞춘다.) `tests/test_transcript.py`에 파일 이름이 `_job{uuid}_row` 꼴인지 보는 단언을 더한다. 정수 ID를 쓰던 테스트(`/results/1`, `store.sources(1)` 등)는 만든 작업의 `job.id`를 쓰도록 고친다.

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 위 Interfaces대로. 페이지의 링크·폼 action은 `job.id`(문자열)를 쓴다. 숫자 주소는 `job_or_404`가 None을 받아 404. worker의 `Submit`, `_run`, `_process`, `resume`도 str.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체(Task 3·4 범위의 화면·스크립트 테스트가 아직 깨지면 보고서에 이름을 적고, 그 테스트는 다음 작업이 고친다는 것을 적는다. 단, 이 작업이 바꾼 파일의 테스트는 모두 통과해야 한다).
- [ ] **Step 5: 커밋** — `feat: UUID job URLs and log names, job title and settings recorded at start, rename`

---

### Task 3: 화면 — 작업 설정 카드, 목록 칸, 이름 바꾸기 양식, 요약 제목

**Files:**
- Modify: `src/llm_lab/pages.py`
- Test: `tests/test_pages.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `Job.title`, `Job.settings`(JSON), Task 2의 rename 라우트, kind의 `modes`(값→라벨).
- Produces:
  - 작업 화면: `<h1>`이 `job.title`(비었으면 예전처럼 `{excel_name}`), 옆에 접기 양식(`<details><summary>이름 바꾸기</summary><form method="post" action="/results/{id}/rename">…`). 그 아래 "작업 설정" 카드: 연결 / 모델 / 시트 종류 / 처리 방식(라벨) / 분석 대상(쉼표) / 되묻기 횟수 / 소스 폴더 / 엑셀 파일 / 시작 시각. 값이 없으면 "-". settings가 비면(예전 형식은 없지만 안전하게) 카드에 "기록된 설정이 없습니다".
  - 첫 화면 최근 작업과 작업 목록 표: 이름(링크) / 종류 / 연결 / 모델 / 시작 / 진행.
  - 요약 페이지: 제목 `작업 요약 · {kind.title} · {job.id[:8]}`, 모델·연결·작업 이름 없음.
  - 브라우저 탭 제목: 작업 화면은 `job.title`.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_pages.py`에 `job_page`, `results_page`(작업 목록), `summary_page`를 직접 부르는 테스트:
  - settings에 `{"connection": "게이트웨이 B", "model": "secret-model", "kind": "rte", "mode": "guard", "choices": ["Orange","Red"], "follow_ups": 2, "source_root": "D:/secret", "excel": "secret.xlsx"}`, title `"secret 작업"`인 Job으로
    - 작업 화면에 "작업 설정", "게이트웨이 B", "secret-model", "가드 허용", "Orange, Red", "이름 바꾸기"가 있음
    - 작업 목록에 이름·연결·모델 칸이 있음
    - 요약 페이지 HTML에 `"secret"`이 없고 제목이 `작업 요약 · RTE · {id[:8]}`
  (기존 페이지 테스트의 Job 만드는 도우미를 쓰고, 없으면 `Job(id=..., created_at=..., excel_name=..., sheet=..., skipped=0, state="done", message="", title=..., settings=json.dumps(...))`.)
- [ ] **Step 2~4:** 실패 확인 → 구현 → `uv run pytest -q` 전체 통과
- [ ] **Step 5: 커밋** — `feat: job settings card, model and connection in job lists, rename form, neutral summary title`

---

### Task 4: 점검 스크립트를 UUID로

**Files:**
- Modify: `scripts/peek_logs.py`, `scripts/peek_job.py`(그리고 작업 ID를 쓰는 다른 scripts가 있으면)
- Test: `tests/test_peek_logs.py`, `tests/test_peek_job.py`

**Interfaces:**
- Produces: `--job`은 문자열. 공용 도우미(각 스크립트 안 또는 `llm_lab.store`의 `Store.find_job(prefix: str) -> Job | None | list[Job]`)로 4자 이상 앞부분을 찾는다. 하나면 그 작업, 여럿이면 "여러 작업이 맞습니다. 더 길게 적으세요.", 없으면 "작업을 찾지 못했습니다. --job에 작업 ID 앞부분을 적으세요." 기본값은 가장 최근 작업(`Store.jobs(limit=1)`). 기록 파일 찾기 정규식은 `_job([0-9a-f-]{36})_row\d+`. 화면의 "작업 N" 자리는 `작업 {id[:8]}`.

- [ ] **Step 1: 실패하는 테스트** — 두 스크립트 테스트에 UUID 작업으로 기록·DB를 만들고 `--job`에 앞 8자를 넘겨 찾는지, 여러 개·없음 안내, 화면에 `secret`이 없고 작업이 앞 8자로만 찍히는지. 기존 테스트의 정수 작업 번호(3, 9 등)는 UUID로 고친다.
- [ ] **Step 2~4:** 실패 확인 → 구현 → `uv run pytest -q` 전체 통과
- [ ] **Step 5: 커밋** — `feat: peek scripts find jobs by UUID prefix`

---

### Task 5: 문서와 할 일

- [ ] README: 작업 ID가 UUID이고 주소에 보인다, 작업 이름 바꾸기, 작업 설정 카드, 점검 스크립트 `--job`에 ID 앞부분. 예전 기록은 `--clear all` 뒤 새로 시작.
- [ ] `docs/commands.md`: peek 스크립트 `--job` 설명을 "작업 ID 앞부분"으로.
- [ ] `todo.md`: 5절 뒤에 6절 "작업 ID와 이름 확인" — `git pull` → 서버를 끈 상태에서 `uv run llm-fix-server --clear all`(프로필이면 `--profile 이름`) → 서버 켜기 → 분석 하나 → 주소에 긴 ID가 보이는지, "이름 바꾸기"가 되는지, "작업 설정" 카드가 보이는지(예/아니오). 화면 캡처는 요청하지 않는다(모델·이름이 보임).
- [ ] 스펙 상태 `구현됨 (2026-10-15)`.
- [ ] `uv run ruff check .`, `uv run pytest -q` 후 커밋 — `docs: UUID jobs, job names and settings in README, commands and todo`
