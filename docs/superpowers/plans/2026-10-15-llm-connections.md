# LLM 연결 설정 페이지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 설정 페이지에서 LLM 연결을 추가·수정·삭제·선택하고, 위쪽 막대에 현재 연결을 보이고, 이름을 LLM Test Advisor로 바꾼다.

**Architecture:** 새 모듈 `llm_lab/connections.py`가 연결 값(`Connection`)과 저장소(`ConnectionStore`, `private/connections.json`)를 맡는다. `.env` 연결은 읽기 전용 항목으로 들어간다. 작업은 시작할 때 연결 이름을 기록하고(`jobs.connection`), worker는 작업의 연결로 클라이언트를 만들어 쓴다. 설정 페이지와 위쪽 막대는 `pages.py`·`web.py`에 더한다.

**Tech Stack:** Python 3.14, FastAPI(HTML 폼, JS 없음), openai SDK, sqlite, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-15-llm-connections-design.md`

## Global Constraints

- 사용자에게 보이는 글·주석은 한국어. 이름은 정확히 `LLM Test Advisor`. 첫 화면 설명은 정확히 "정적 분석 결과와 소스 코드를 묶어 LLM에 묻고, 판단과 수정안을 받습니다."
- 키(`api_key`)와 프로젝트 ID는 어떤 페이지 HTML에도 나오지 않는다(테스트는 `secret`이 든 값으로 확인).
- 위쪽 막대에는 연결 이름만(모델 이름 없음). 캡처용 요약 페이지(`/results/N/summary`)에는 연결 이름도 없음.
- `LLM_WORKERS`, `LLM_FOLLOW_UPS`는 `.env`에 남는 서버 전체 설정이다. 연결에 넣지 않는다.
- 저장 파일: 데이터 폴더(`private/` 또는 `private/프로필/`)의 `connections.json`, 형식 `{"current": "이름", "connections": [...]}`. 쓰기는 임시 파일 + `os.replace`.
- 오류는 종류와 할 일만 보인다(경로·원문 없음).
- 검사: `uv run ruff format src tests scripts`(`.` 아님), `uv run ruff check .`, `uv run pytest -q`.
- 커밋: 영어 한 줄 + 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. push하지 않는다.

## Review Focus

- 수정 양식에서 키 칸을 비우면 저장된 키가 그대로 남아야 한다(빈 키로 덮어쓰면 다음 분석이 401). Task 2 테스트.
- 처리 중에 현재 연결을 바꿔도 이미 시작한 작업은 시작할 때의 연결로 요청해야 한다. Task 3 테스트.
- 작업이 쓰던 연결이 지워지면 행이 오류로 쌓이지 않고 작업이 멈춰야 한다. Task 3 테스트.
- 연결이 하나도 없을 때 "분석 시작"이 엑셀을 다 읽은 뒤가 아니라 처음에 막혀야 한다. Task 4 테스트.
- `connections.json`이 깨져 있어도 서버가 켜지고 설정 페이지가 이유를 보여야 한다. Task 2 테스트.

---

### Task 1: 이름과 첫 화면 제목

**Files:**
- Modify: `src/llm_lab/pages.py` (`APP_NAME`, `index_page`)
- Modify: `src/llm_lab/web.py` (서버 시작 안내 문구에 이름이 있으면)
- Modify: `README.md` 첫 줄
- Test: `tests/test_pages.py`

**Interfaces:**
- Produces: `pages.APP_NAME = "LLM Test Advisor"`, `pages.APP_TAGLINE = "정적 분석 결과와 소스 코드를 묶어 LLM에 묻고, 판단과 수정안을 받습니다."`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_pages.py` 끝에:

```python
def test_app_name_and_tagline_on_the_home_page():
    html = index_page([], [])

    assert pages.APP_NAME == "LLM Test Advisor"
    assert "<title>새 분석 · LLM Test Advisor</title>" in html
    assert '<h1 class="app-title">LLM Test Advisor</h1>' in html
    assert pages.APP_TAGLINE in html
    assert "Polyspace 수정 제안" not in html
```

(`pages`, `index_page` import가 없으면 맨 위에 `from llm_lab import pages`, `from llm_lab.pages import index_page`를 더한다.)

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_pages.py -q` → FAIL
- [ ] **Step 3: 구현**
  - `APP_NAME = "LLM Test Advisor"`, 그 아래 `APP_TAGLINE = "정적 분석 결과와 소스 코드를 묶어 LLM에 묻고, 판단과 수정안을 받습니다."`
  - `index_page`의 본문 맨 앞(지금의 `<h1>새 분석</h1>` 자리)을
    `f'<h1 class="app-title">{APP_NAME}</h1><p class="sub">{APP_TAGLINE}</p><h2>새 분석</h2>'`로 바꾼다. 페이지 제목(`page("새 분석", ...)`)은 그대로.
  - `STYLE`에 `.app-title{margin-bottom:2px}` 정도만 더한다(없어도 됨).
  - `README.md` 첫 줄 제목을 `# LLM Test Advisor`로 바꾸고, 바로 아래에 `정적 분석 결과와 소스 코드를 묶어 LLM에 묻고, 판단과 수정안을 받는 도구입니다. 서버 명령 이름(llm-fix-server 등)은 그대로입니다.`를 한 줄 넣는다(기존 설명 문단은 그대로 둔다).
  - "Polyspace 수정 제안"이 다른 곳(테스트 포함)에 남아 있으면 `APP_NAME`을 쓰도록 고친다.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체 PASS
- [ ] **Step 5: 커밋** — `feat: rename the app to LLM Test Advisor with a tagline on the home page`

---

### Task 2: 연결 값과 저장소

**Files:**
- Create: `src/llm_lab/connections.py`
- Modify: `src/llm_lab/config.py` (`settings_from_env(..., require_connection=True)`, `load_settings(..., require_connection=True)`)
- Test: `tests/test_connections.py` (새 파일), `tests/test_config.py`

**Interfaces:**
- Produces (`llm_lab.config`): `settings_from_env(env, require_connection: bool = True) -> Settings` — False면 `LLM_BASE_URL`·`LLM_MODEL`이 비어도 오류 없이 `base_url=""`, `model=""`. `load_settings(profile=None, require_connection=True)` — False면 `.env`/`.env.이름` 파일이 없어도 기본값 Settings.
- Produces (`llm_lab.connections`):
  - `ENV_NAME = ".env"`
  - `@dataclass(frozen=True) class Connection`: `name: str`, `client_kind: str`, `base_url: str`, `api_key: str` (repr=False), `model: str`, `project_id: str = ""` (repr=False), `project_header: str = DEFAULT_PROJECT_HEADER`, `api_version: str = ""`, `timeout: float = DEFAULT_TIMEOUT`, `verify_ssl: bool = True`, `enable_thinking: bool | None = None`, `read_only: bool = False`
  - `Connection.settings(base: Settings) -> Settings` — `base`의 workers·follow_ups에 이 연결의 값을 얹은 Settings(`dataclasses.replace`).
  - `Connection.fingerprint() -> str` — 모든 값의 sha256(클라이언트 캐시 무효화용).
  - `connection_from_settings(name: str, settings: Settings, read_only: bool) -> Connection`
  - `connection_from_form(fields: Mapping[str, str], keep: Connection | None = None) -> Connection` — 폼 값(`name, client_kind, base_url, api_key, model, project_id, project_header, api_version, timeout, verify_ssl("true"/"false"), enable_thinking(""/"true"/"false")`)을 `settings_from_env`와 같은 규칙으로 검사(값을 `LLM_*` 이름 사전으로 바꿔 `settings_from_env`를 부른다). `api_key`·`project_id`가 비었고 `keep`이 있으면 keep의 값을 쓴다. 이름이 비면 `ConfigError("연결 이름을 적으세요")`.
  - `class ConnectionStore(path: Path, env: Connection | None = None)`:
    - `error: str` — 파일을 못 읽었으면 종류(`"JSONDecodeError"` 등), 아니면 `""`.
    - `list() -> list[Connection]` — env 연결(있으면) 맨 앞, 그다음 저장 순서.
    - `get(name) -> Connection | None`, `current() -> Connection | None` (저장된 current가 없거나 없는 이름이면 첫 연결)
    - `save(connection: Connection, original: str | None = None) -> None` — 추가(original None) 또는 수정. 같은 이름이 있으면(자기 자신 제외) `ConfigError("같은 이름의 연결이 있습니다")`. read_only(.env)를 고치려 하면 `ConfigError(".env 연결은 .env 파일에서 고칩니다")`. 이름이 바뀌고 그게 current였으면 current도 바꾼다.
    - `delete(name) -> None` — current면 `ConfigError("현재 연결은 지울 수 없습니다. 다른 연결을 먼저 고르세요")`, read_only면 위와 같은 오류.
    - `select(name) -> None` — 없는 이름이면 `ConfigError("없는 연결입니다")`.
    - 모든 메서드는 `threading.Lock` 안에서 동작. 파일에는 read_only 연결을 쓰지 않는다.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_connections.py`:

```python
"""LLM 연결 값과 저장소 (connections.py)."""

import json

import pytest

from llm_lab.config import ConfigError, Settings
from llm_lab.connections import (
    ENV_NAME,
    Connection,
    ConnectionStore,
    connection_from_form,
    connection_from_settings,
)

FORM = {
    "name": "게이트웨이 B",
    "client_kind": "openai",
    "base_url": "https://llm.test/v1/",
    "api_key": "secret-key",
    "model": "secret-model",
    "project_id": "",
    "project_header": "",
    "api_version": "",
    "timeout": "",
    "verify_ssl": "true",
    "enable_thinking": "",
}


def test_form_is_checked_with_the_env_rules():
    connection = connection_from_form(FORM)

    assert connection.base_url == "https://llm.test/v1"  # 끝의 / 정리는 settings_from_env 규칙
    assert connection.api_key == "secret-key" and connection.timeout == 60.0
    with pytest.raises(ConfigError, match="LLM_TIMEOUT"):
        connection_from_form({**FORM, "timeout": "abc"})
    with pytest.raises(ConfigError, match="이름"):
        connection_from_form({**FORM, "name": " "})
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        connection_from_form({**FORM, "client_kind": "azure", "api_key": ""})


def test_blank_secrets_on_edit_keep_the_saved_ones():
    saved = connection_from_form({**FORM, "project_id": "secret-project"})

    edited = connection_from_form({**FORM, "api_key": "", "project_id": "", "model": "m2"}, keep=saved)

    assert edited.api_key == "secret-key" and edited.project_id == "secret-project"
    assert edited.model == "m2"
    assert "secret" not in repr(edited).replace("secret-model", "")


def test_store_adds_selects_edits_and_deletes(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    a = connection_from_form({**FORM, "name": "A"})
    b = connection_from_form({**FORM, "name": "B"})

    store.save(a)
    store.save(b)
    assert [c.name for c in store.list()] == ["A", "B"]
    assert store.current().name == "A"
    store.select("B")
    assert ConnectionStore(path).current().name == "B"  # 파일에 남는다

    store.save(connection_from_form({**FORM, "name": "B2"}), original="B")
    assert store.current().name == "B2"
    with pytest.raises(ConfigError, match="같은 이름"):
        store.save(connection_from_form({**FORM, "name": "A"}), original="B2")
    with pytest.raises(ConfigError, match="현재 연결"):
        store.delete("B2")
    store.delete("A")
    assert [c.name for c in store.list()] == ["B2"]
    assert json.loads(path.read_text(encoding="utf-8"))["current"] == "B2"


def test_env_connection_is_listed_first_and_read_only(tmp_path):
    settings = Settings(base_url="https://env.test/v1", api_key="secret-env", model="m")
    env = connection_from_settings(ENV_NAME, settings, read_only=True)
    store = ConnectionStore(tmp_path / "connections.json", env)

    assert store.current() is env
    store.save(connection_from_form({**FORM, "name": "A"}))
    assert [c.name for c in store.list()] == [ENV_NAME, "A"]
    assert store.current().name == ENV_NAME
    with pytest.raises(ConfigError, match=".env"):
        store.save(connection_from_form({**FORM, "name": ENV_NAME}), original=ENV_NAME)
    with pytest.raises(ConfigError, match=".env"):
        store.delete(ENV_NAME)
    assert ENV_NAME not in (tmp_path / "connections.json").read_text(encoding="utf-8")


def test_broken_file_leaves_an_error_kind_and_an_empty_list(tmp_path):
    path = tmp_path / "connections.json"
    path.write_text("{not json", encoding="utf-8")

    store = ConnectionStore(path)

    assert store.error == "JSONDecodeError"
    assert store.list() == [] and store.current() is None


def test_connection_settings_keep_server_options():
    base = Settings(base_url="", api_key="", model="", workers=8, follow_ups=1)
    connection = connection_from_form(FORM)

    settings = connection.settings(base)

    assert settings.workers == 8 and settings.follow_ups == 1
    assert settings.model == "secret-model" and settings.api_key == "secret-key"
    assert connection.fingerprint() != connection_from_form({**FORM, "model": "x"}).fingerprint()
```

`tests/test_config.py` 끝에:

```python
def test_settings_without_a_connection_are_allowed_when_asked():
    settings = settings_from_env({"LLM_WORKERS": "4"}, require_connection=False)

    assert settings.base_url == "" and settings.model == "" and settings.workers == 4
    with pytest.raises(ConfigError):
        settings_from_env({"LLM_WORKERS": "4"})


def test_load_settings_without_env_file_when_asked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    assert load_settings(require_connection=False).base_url == ""
    assert load_settings("b", require_connection=False).base_url == ""
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_connections.py tests/test_config.py -q` → FAIL
- [ ] **Step 3: 구현**
  - `config.py`: `settings_from_env(env, require_connection=True)`에서 `missing`이 있으면 `require_connection`일 때만 `MissingSettingsError`. False면 비어 있어도 진행(base_url·model은 ""). azure 키 검사는 base_url이 있을 때만. `load_settings(profile=None, require_connection=True)`: False면 프로필 파일이 없어도 오류 없이 OS 환경변수만으로 만든다(기존 "프로필 설정 파일이 없습니다" 오류는 True일 때만).
  - `connections.py`: 위 Interfaces 그대로. `connection_from_form`은
    `{"LLM_BASE_URL": base_url, "LLM_MODEL": model, "LLM_API_KEY": api_key or keep.api_key, "LLM_PROJECT_ID": ..., "LLM_PROJECT_HEADER": ..., "LLM_TIMEOUT": timeout, "LLM_VERIFY_SSL": verify_ssl, "LLM_ENABLE_THINKING": enable_thinking, "LLM_CLIENT": client_kind, "LLM_API_VERSION": api_version}`로 `settings_from_env`를 불러 검사하고 `connection_from_settings(name.strip(), settings, read_only=False)`를 돌려준다.
  - JSON에는 read_only가 아닌 연결만 `dataclasses.asdict`에서 `read_only`를 뺀 사전으로 쓴다. 읽을 때 모르는 칸은 버린다.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체 PASS
- [ ] **Step 5: 커밋** — `feat: LLM connections with a JSON store and an env connection`

---

### Task 3: 작업에 연결 기록, worker가 작업의 연결로 요청

**Files:**
- Modify: `src/llm_lab/store.py` (`jobs.connection` 칸, `Job.connection`, `create_job(..., connection="")`)
- Modify: `src/llm_lab/worker.py`
- Test: `tests/test_worker_connections.py` (새 파일), `tests/test_store.py`

**Interfaces:**
- Consumes: Task 2의 `ConnectionStore`, `Connection.settings`, `Connection.fingerprint`.
- Produces: `Store.create_job(..., connection: str = "")`, `Job.connection: str = ""`.
  `Worker(store, client, model, submit, log_dir=None, follow_ups=0, indexes=None, connections: ConnectionStore | None = None, base_settings: Settings | None = None, make_client=get_client)`.
  connections가 None이면 지금처럼 `client`·`model`을 쓴다. 있으면 작업마다 `job.connection`(빈 값이면 현재 연결)의 연결로 `make_client(connection.settings(base_settings))`를 만들어 쓴다(이름·fingerprint별로 캐시, 잠금).
  연결을 못 찾으면 그 묶음의 행을 pending으로 되돌리고 작업을 `stopped`로, 메시지 `MISSING_CONNECTION = "작업의 LLM 연결이 없어졌습니다. 설정에서 연결을 고른 뒤 다시 시도하세요."`.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_store.py` 끝에:

```python
def test_job_keeps_the_connection_name(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job = store.create_job("x.xlsx", "RTE_Result", 0, [], [], connection="게이트웨이 B")
    old = store.create_job("x.xlsx", "RTE_Result", 0, [], [])

    assert store.job(job).connection == "게이트웨이 B"
    assert store.job(old).connection == ""
```

`tests/test_worker_connections.py`:

```python
"""worker가 작업에 기록된 연결로 요청한다."""

from fake_llm import MODEL, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.config import Settings
from llm_lab.connections import ConnectionStore, connection_from_form
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import MISSING_CONNECTION, Worker

SOURCE = decode_source("calc.c", CALC_C)
FORM = {"client_kind": "openai", "base_url": "https://llm.test/v1", "api_key": "k", "model": MODEL}


def run_now(task):
    task()


def setup(tmp_path):
    connections = ConnectionStore(tmp_path / "connections.json")
    connections.save(connection_from_form({**FORM, "name": "A"}))
    connections.save(connection_from_form({**FORM, "name": "B", "model": "model-b"}))
    handler_a, sent_a = scripted(fix_answer(), fix_answer())
    handler_b, sent_b = scripted(fix_answer(), fix_answer())
    handlers = {MODEL: handler_a, "model-b": handler_b}
    made = []

    def make(settings: Settings):
        made.append(settings.model)
        return make_client(handlers[settings.model])

    store = Store(tmp_path / "fixer.db")
    queued = []
    worker = Worker(
        store, None, "", queued.append, connections=connections,
        base_settings=Settings(base_url="", api_key="", model=""), make_client=make,
    )
    return store, worker, connections, queued, sent_a, sent_b, made


def new_job(store, connection):
    return store.create_job(
        "x.xlsx", "RTE_Result", 0, [SOURCE], [NewRow(finding(), "calc.c", "pending")],
        connection=connection,
    )


def test_job_uses_its_recorded_connection_even_after_the_current_one_changes(tmp_path):
    store, worker, connections, queued, sent_a, sent_b, made = setup(tmp_path)
    job = new_job(store, "A")
    worker.start(job)
    connections.select("B")  # 처리 중에 현재 연결을 바꿔도

    for task in queued:
        task()

    assert len(sent_a) == 1 and sent_b == []
    assert store.rows(job)[0].state == "done"


def test_old_job_without_a_name_uses_the_current_connection(tmp_path):
    store, worker, connections, queued, sent_a, sent_b, made = setup(tmp_path)
    connections.select("B")
    job = new_job(store, "")
    worker.start(job)
    for task in queued:
        task()

    assert len(sent_b) == 1 and sent_a == []


def test_clients_are_reused_per_connection(tmp_path):
    store, worker, connections, queued, sent_a, sent_b, made = setup(tmp_path)
    for _ in range(2):
        worker.start(new_job(store, "A"))
    for task in queued:
        task()

    assert made == [MODEL]


def test_deleted_connection_stops_the_job(tmp_path):
    store, worker, connections, queued, sent_a, sent_b, made = setup(tmp_path)
    job = new_job(store, "A")
    worker.start(job)
    connections.select("B")
    connections.delete("A")
    for task in queued:
        task()

    assert store.job(job).state == "stopped"
    assert store.job(job).message == MISSING_CONNECTION
    assert store.rows(job)[0].state == "pending"
    assert sent_a == [] and sent_b == []
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_store.py tests/test_worker_connections.py -q` → FAIL
- [ ] **Step 3: 구현**
  - store: `ADDED_COLUMNS`에 `("jobs", "connection", "TEXT NOT NULL DEFAULT ''")`, SCHEMA의 jobs 표에도 같은 칸, `Job.connection: str = ""`, `create_job`의 INSERT에 connection. `SCHEMA_VERSION`을 올리고 주석을 단다(기존 방식 따름).
  - worker: `_process`에서 `make_request` 전에 `client, model = self._llm(job)`을 구하고, 이후 `self.client`·`self.model` 대신 그것을 쓴다(`cache_key`, `propose_fixes`, `_save_log`의 model·endpoint 포함 — `_save_log`에 client·model 인자를 더한다). `_llm`이 `LookupError`를 내면 `FatalLLMError` 경로와 같이 행을 pending으로 돌리고 `store.set_job(job_id, "stopped", MISSING_CONNECTION)` 후 return(기록 파일은 남기지 않는다).
  - 캐시: `self._clients: dict[str, tuple[str, OpenAI]]`(이름 → (fingerprint, client)), `threading.Lock`.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체 PASS (기존 worker 테스트는 connections 없이 그대로)
- [ ] **Step 5: 커밋** — `feat: jobs record their LLM connection and the worker requests through it`

---

### Task 4: 설정 페이지, 위쪽 막대, 분석 시작 연결

**Files:**
- Modify: `src/llm_lab/pages.py` (`page(..., connection: str | None = None)` 위쪽 막대, `settings_page`, `index_page` 연결 없음 안내, `job_page` 부제, `summary_page`는 연결 없이)
- Modify: `src/llm_lab/web.py` (`create_app(store, worker, connections=None)`, `/settings` 라우트, 분석 시작에서 연결 확인·기록, `main` 연결)
- Test: `tests/test_settings_page.py` (새 파일), `tests/test_web.py`

**Interfaces:**
- Consumes: Task 2 `ConnectionStore`, `connection_from_form`, `ConfigError`; Task 3 `create_job(connection=)`, `Worker(connections=, base_settings=)`.
- Produces:
  - `pages.page(title, body, *, refresh=False, connection: str | None = None)` — connection이 None이 아니면 위쪽 막대 오른쪽에 `<a class="conn" href="/settings">연결: {이름 또는 "없음"}</a>`. None이면 아무것도 넣지 않는다(요약 페이지).
  - `pages.settings_page(connections: list[Connection], current: str | None, message: str = "", form: Mapping[str, str] | None = None, editing: str | None = None, checks: Mapping[str, str] | None = None, error: str = "") -> str`
  - web 라우트(모두 폼 POST 뒤 `/settings`로 303 이동, 검사 오류면 양식을 다시 그린 200):
    - `GET /settings` — 목록과 추가 양식. `?edit=이름`이면 그 연결의 수정 양식(키·프로젝트 ID 칸은 비움, 안내 "비워 두면 저장된 값을 씁니다").
    - `POST /settings/save` — 폼 + `original`(수정이면 원래 이름).
    - `POST /settings/select` — `name`.
    - `POST /settings/delete` — `name` (HTML `onsubmit="return confirm(...)"` 확인).
  - 다른 페이지들은 `page(..., connection=current_name)`으로 막대를 보인다. 구현 방식: `create_app`이 `pages.CONNECTION_LABEL = lambda: ...`처럼 모듈 함수를 설정하지 말고, 각 라우트가 `label()`(현재 연결 이름 또는 "")을 페이지 함수에 넘긴다. 페이지 함수들(`index_page`, `jobs_page`, `job_page`, `error_page`, `settings_page`)에 `connection: str | None = None` 키워드를 더하고 `page(...)`에 그대로 넘긴다. `summary_page`는 넘기지 않는다.
  - 분석 시작(`POST /results`): connections가 있고 `current()`가 None이면 엑셀을 읽기 전에 `error_page("LLM 연결이 없음", "설정에서 LLM 연결을 추가하고 고른 뒤 다시 시작하세요.")`, 400. 있으면 `create_job(..., connection=current.name)`.
  - 첫 화면: 연결이 없으면 양식 위에 `<div class="card warn">설정에서 LLM 연결을 추가하세요 <a href="/settings">설정</a></div>`.
  - 작업 화면 부제: `· 연결 {job.connection}`(비었으면 넣지 않음).
  - `main`: `load_settings(args.profile, require_connection=False)`. base_url이 있으면 `connection_from_settings(ENV_NAME, settings, read_only=True)`를 env로. `ConnectionStore(data_dir / "connections.json", env)`. Worker는 `connections=`, `base_settings=settings`, client는 None. 시작 안내에 연결이 없으면 "LLM 연결이 없습니다: 접속 주소의 설정에서 추가하세요" 한 줄.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_settings_page.py`:

```python
"""설정 페이지와 위쪽 막대 (web + pages)."""

from fastapi.testclient import TestClient
from fake_llm import MODEL, make_client, scripted
from rte_fixtures import fix_answer

from llm_lab.config import Settings
from llm_lab.connections import ENV_NAME, ConnectionStore, connection_from_settings
from llm_lab.store import Store
from llm_lab.web import create_app
from llm_lab.worker import Worker

FORM = {
    "name": "게이트웨이 B",
    "client_kind": "openai",
    "base_url": "https://llm.test/v1",
    "api_key": "secret-key-123",
    "model": "model-b",
    "project_id": "secret-project",
    "project_header": "",
    "api_version": "",
    "timeout": "",
    "verify_ssl": "true",
    "enable_thinking": "",
}


def service(tmp_path, env=True):
    store = Store(tmp_path / "fixer.db")
    base = Settings(base_url="https://env.test/v1", api_key="secret-env", model=MODEL)
    connections = ConnectionStore(
        tmp_path / "connections.json",
        connection_from_settings(ENV_NAME, base, read_only=True) if env else None,
    )
    handler, _ = scripted(fix_answer())
    worker = Worker(
        store, None, "", lambda task: None, connections=connections, base_settings=base,
        make_client=lambda settings: make_client(handler),
    )
    client = TestClient(create_app(store, worker, connections), base_url="http://127.0.0.1")
    return client, connections


def test_add_select_and_show_the_current_connection_without_secrets(tmp_path):
    client, connections = service(tmp_path)

    assert client.post("/settings/save", data=FORM, follow_redirects=False).status_code == 303
    client.post("/settings/select", data={"name": "게이트웨이 B"})
    html = client.get("/settings").text
    home = client.get("/").text

    assert "게이트웨이 B" in html and "model-b" in html and "현재 연결" in html
    assert "연결: 게이트웨이 B" in home
    assert "model-b" not in home  # 위쪽 막대에는 모델 이름이 없다
    for text in (html, home):
        assert "secret" not in text


def test_edit_with_blank_key_keeps_it(tmp_path):
    client, connections = service(tmp_path)
    client.post("/settings/save", data=FORM)

    form = client.get("/settings", params={"edit": "게이트웨이 B"}).text
    client.post("/settings/save", data={**FORM, "api_key": "", "project_id": "", "model": "m2",
                                        "original": "게이트웨이 B"})

    assert 'name="original" value="게이트웨이 B"' in form and "secret" not in form
    saved = connections.get("게이트웨이 B")
    assert saved.api_key == "secret-key-123" and saved.model == "m2"


def test_invalid_form_shows_the_reason(tmp_path):
    client, _ = service(tmp_path)

    response = client.post("/settings/save", data={**FORM, "timeout": "abc"})

    assert response.status_code == 200 and "LLM_TIMEOUT" in response.text
    assert "secret" not in response.text


def test_env_connection_has_no_edit_or_delete(tmp_path):
    client, _ = service(tmp_path)

    html = client.get("/settings").text

    assert ".env 파일에서 고침" in html
    assert client.post("/settings/delete", data={"name": ENV_NAME}).status_code == 200


def test_no_connection_blocks_analysis_before_reading_the_excel(tmp_path):
    client, _ = service(tmp_path, env=False)

    home = client.get("/").text
    response = client.post(
        "/results", files={"excel": ("x.xlsx", b"not an excel")}, data={"source_dir": str(tmp_path)}
    )

    assert "연결: 없음" in home and "설정에서 LLM 연결을 추가하세요" in home
    assert response.status_code == 400 and "LLM 연결이 없음" in response.text
```

`tests/test_web.py`에 요약 페이지에 연결 이름이 없다는 테스트를 하나 더한다(기존 요약 페이지 테스트가 쓰는 준비 함수를 써서 `"연결:" not in summary_html`).

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_settings_page.py -q` → FAIL
- [ ] **Step 3: 구현** — Interfaces대로. 설정 페이지 모양은 첫 화면의 카드 모양(`card`, `step` 클래스)을 따르고, 목록은 연결마다 한 줄 카드(이름, 현재 연결 배지, `접속 방식 · 모델 · 주소`, 버튼 폼들). 추가·수정 양식은 위 FORM의 칸들 + `original` 숨은 칸. 키·프로젝트 ID는 `type="password"`이고 value를 채우지 않는다. 오류 메시지는 `ConfigError`의 글을 그대로 보인다(경로·키가 들어가지 않는 글이다).
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` 전체 PASS
- [ ] **Step 5: 커밋** — `feat: settings page to add, edit, delete and select LLM connections; current connection in the top bar`

---

### Task 5: 연결 확인 버튼

**Files:**
- Modify: `src/llm_lab/connections.py` (`check_connection`)
- Modify: `src/llm_lab/web.py` (`POST /settings/check`), `src/llm_lab/pages.py` (목록에 결과 표시)
- Test: `tests/test_connections.py`, `tests/test_settings_page.py`

**Interfaces:**
- Produces: `check_connection(connection: Connection, base: Settings, make_client=get_client) -> str` — 짧은 chat 요청 하나(`probe.check_chat`)를 보내고 결과 글 하나를 돌려준다: `"성공"` / `"인증 실패"` / `"주소·모델 오류"` / `"연결 실패"` / `"TLS·인증서 오류"` / `"그 밖의 오류"`. `openai.APIError`는 `probe.classify_error`의 분류로 고른다(404·NotFound는 "주소·모델 오류"). 오류 원문은 돌려주지 않는다. 타임아웃은 10초로 줄인 클라이언트(`client.with_options(timeout=10, max_retries=0)`).
- `POST /settings/check` — `name`. 결과를 앱 메모리(`dict[이름, 결과]`)에 두고 `/settings`로 303. 목록에 `연결 확인: 결과`(성공은 녹색, 나머지는 빨간 글).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_connections.py` 끝에:

```python
def test_check_connection_reports_kinds_without_raw_text():
    from fake_llm import completion, error, make_client, scripted

    from llm_lab.connections import check_connection

    base = Settings(base_url="", api_key="", model="")
    connection = connection_from_form(FORM)
    ok, _ = scripted(completion("pong"))
    denied, _ = scripted(error(401, "secret detail"))
    missing, _ = scripted(error(404, "secret detail"))

    assert check_connection(connection, base, lambda s: make_client(ok)) == "성공"
    assert check_connection(connection, base, lambda s: make_client(denied)) == "인증 실패"
    assert check_connection(connection, base, lambda s: make_client(missing)) == "주소·모델 오류"
```

`tests/test_settings_page.py` 끝에:

```python
def test_check_button_shows_the_result_kind(tmp_path, monkeypatch):
    from llm_lab import web

    client, _ = service(tmp_path)
    monkeypatch.setattr(web, "check_connection", lambda connection, base, make_client=None: "인증 실패")

    client.post("/settings/check", data={"name": ENV_NAME})

    assert "연결 확인: 인증 실패" in client.get("/settings").text
```

- [ ] **Step 2~4:** 실패 확인 → 구현 → `uv run pytest -q` 전체 PASS
- [ ] **Step 5: 커밋** — `feat: check an LLM connection from the settings page`

---

### Task 6: 문서

**Files:** `README.md`, `docs/company-checklist.md`, `docs/commands.md`(있으면 설정 페이지 한 줄), `todo.md`, 스펙 상태 줄.

- [ ] README "실행" 부분에: 접속 주소의 "설정"(위쪽 막대의 "연결: …")에서 LLM 연결을 추가·선택할 수 있고, `.env`의 연결은 `.env`라는 이름으로 자동으로 들어가며, `LLM_WORKERS`·`LLM_FOLLOW_UPS`는 계속 `.env`에서 정한다는 설명 3~4줄.
- [ ] `docs/company-checklist.md`: `.env` 대신 설정 페이지에서도 연결을 넣을 수 있다는 한 줄.
- [ ] `todo.md` 끝에 새 절: `git pull` → 서버 다시 켜기 → 위쪽 막대에 "연결: .env"가 보이는지 → 설정에서 연결을 하나 추가하고 "연결 확인" → 현재 연결로 골라 작은 분석 하나. 알려줄 것: 연결 확인 결과 종류, 막대 표시가 바뀌었는지, 분석이 새 연결로 됐는지(대화 기록의 모델 이름은 적지 말고 예/아니오).
- [ ] 스펙 상태를 `구현됨 (2026-10-15)`로.
- [ ] `uv run ruff check .`, `uv run pytest -q` 후 커밋 — `docs: LLM connections settings page in README, checklist and todo`
