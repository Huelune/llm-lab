# 정의 되묻기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM이 답의 `need` 칸에 보이지 않는 정의 이름을 적으면, 서버가 프로젝트에서 찾아 다음 메시지로 보내고 다시 판단하게 한다(RTE·MISRA·CodeMetrics, 묶음당 `LLM_FOLLOW_UPS`번까지).

**Architecture:** 새 모듈 `llm_lab/followups.py`가 되묻기 규칙 글, 답 형식의 `need`, 프로젝트 색인(`ProjectIndex`, `IndexCache`), 이름 찾기(`find_asked` → `Asked`)를 맡는다. `fixer.propose_fixes`는 `ask` 콜백을 받아 대화를 잇고, `worker`가 콜백을 만들어 색인에서 찾고 보낸 정의·파일을 작업에 남기며 저장된 답 재사용 때 그 파일을 되살린다. `peek_logs`는 되묻기를 개수로 센다.

**Tech Stack:** Python 3.14, openai SDK(JSON schema 답), sqlite(`Store`), pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-14-definition-follow-ups-design.md`

## Global Constraints

- 코드 주석·문서·사용자에게 보이는 글은 한국어, LLM 지시문·되묻기 메시지는 영어(스펙 3·4절의 문장을 그대로).
- `LLM_FOLLOW_UPS=0`이면 보내는 메시지·답 형식·재사용 키가 지금과 한 글자도 다르지 않다.
- 한 번에 받는 이름 10개(`MAX_ASKED_NAMES`), 한 번에 싣는 정의 20개(`MAX_ASKED_DEFINITIONS`), 줄 수 합 600줄(`MAX_ASKED_LINES`).
- `LLM_FOLLOW_UPS`: 0~3의 정수, 기본 2.
- 받은 정의를 고칠 수 있는 것은 `edits_definitions`인 종류(RTE)뿐. MISRA·CodeMetrics는 참고만.
- 점검 도구 화면에는 개수와 정해 둔 글만. 파일·함수 이름, 경로, 코드, LLM 이유는 찍지 않는다(테스트는 `secret`이 든 이름으로 확인). 한 화면 40줄, 한 줄 118자 안.
- 줄 길이 100(ruff). 한글이 많은 줄은 나눈다. 검사: `uv run ruff format src tests scripts`, `uv run ruff check .`, `uv run pytest -q`.
- 커밋 메시지는 영어 한 줄 + 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- LLM이 매번 이미 보낸 이름을 되묻는다 → "Already shown above"로 알리고 `LLM_FOLLOW_UPS`번 뒤에는 그 답을 최종으로 쓴다(요청 수가 늘지 않음). Task 4의 마지막 차례 테스트.
- 되묻기 도중 답이 JSON이 아니거나 잘렸다 → 그 차례에서 한 번 다시 묻고 대화를 잇는다. Task 4의 형식 재시도 테스트.
- 색인에는 있는데 파일을 다시 읽지 못한다(그사이 지워짐·인코딩) → 그 정의는 참고만으로 보내고 묶음은 계속된다. Task 6의 테스트.
- 저장된 답이 기댄 파일이 소스 폴더에서 바뀌었거나 없어졌다 → 재사용하지 않고 LLM을 부른다. Task 6의 테스트.
- 작업자 여럿이 같은 작업을 동시에 처리한다 → 색인은 작업마다 한 번만 만든다. Task 3의 스레드 테스트.

---

### Task 1: `LLM_FOLLOW_UPS` 설정

**Files:**
- Modify: `src/llm_lab/config.py`
- Modify: `.env.example`
- Modify: `README.md` (설정 표)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.follow_ups: int` (기본 `DEFAULT_FOLLOW_UPS = 2`), `MAX_FOLLOW_UPS = 3`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_config.py` 끝에 더한다(`pytest`, `ConfigError`, `VALID`, `settings_from_env`는 이미 import되어 있다. 없으면 맨 위 import에 더한다):

```python
def test_follow_ups_default_to_two_and_read_llm_follow_ups():
    assert settings_from_env(VALID).follow_ups == 2
    assert settings_from_env({**VALID, "LLM_FOLLOW_UPS": "0"}).follow_ups == 0
    assert settings_from_env({**VALID, "LLM_FOLLOW_UPS": "3"}).follow_ups == 3


@pytest.mark.parametrize("bad", ["4", "-1", "two", "1.5"])
def test_follow_ups_must_be_a_whole_number_from_0_to_3(bad):
    with pytest.raises(ConfigError, match="LLM_FOLLOW_UPS"):
        settings_from_env({**VALID, "LLM_FOLLOW_UPS": bad})
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_config.py -q`
Expected: 두 테스트 FAIL (`follow_ups` 속성 없음)

- [ ] **Step 3: 구현**

`src/llm_lab/config.py`:
- `OPTIONAL_VARS` 튜플 끝(`"LLM_WORKERS",` 뒤)에 `"LLM_FOLLOW_UPS",`를 더한다.
- `MAX_WORKERS = 16` 아래에:

```python
# 묶음마다 LLM이 보이지 않는 정의를 되물을 수 있는 횟수. 0이면 되묻지 않는다(예전과 같은 요청)
DEFAULT_FOLLOW_UPS = 2
MAX_FOLLOW_UPS = 3
```

- `Settings`의 `workers: int = DEFAULT_WORKERS` 아래에 `follow_ups: int = DEFAULT_FOLLOW_UPS`.
- `settings_from_env`에서 `workers = int(text)` 블록 바로 뒤에:

```python
    follow_ups = DEFAULT_FOLLOW_UPS
    if values["LLM_FOLLOW_UPS"]:
        text = values["LLM_FOLLOW_UPS"]
        if not text.isdigit() or not 0 <= int(text) <= MAX_FOLLOW_UPS:
            raise ConfigError(
                f"LLM_FOLLOW_UPS는 0~{MAX_FOLLOW_UPS}의 정수여야 합니다: {text!r}"
            )
        follow_ups = int(text)
```

- `return Settings(...)`의 `workers=workers,` 뒤에 `follow_ups=follow_ups,`.

`.env.example`의 `LLM_WORKERS=` 줄 바로 아래에:

```text
# LLM이 판단에 필요한데 안 보이는 함수 구현·매크로·타입을 묶음마다 되물을 수 있는 횟수(0~3). 비워 두면 2.
# 0이면 되묻지 않습니다(예전과 같은 요청이라 저장된 답도 그대로 다시 씁니다)
LLM_FOLLOW_UPS=
```

`README.md` 설정 표의 `LLM_WORKERS` 행 바로 아래에:

```markdown
   | `LLM_FOLLOW_UPS` | – | LLM이 안 보이는 정의를 묶음마다 되물을 수 있는 횟수(0~3), 기본 2. 0이면 되묻지 않음 |
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/config.py .env.example README.md tests/test_config.py
git commit -m "feat: LLM_FOLLOW_UPS setting (0-3, default 2)"
```

---

### Task 2: 되묻기 지시문·답 형식과 요청

**Files:**
- Create: `src/llm_lab/followups.py`
- Modify: `src/llm_lab/kinds/base.py` (`asks_definitions` 칸)
- Modify: `src/llm_lab/kinds/rte.py`, `src/llm_lab/kinds/misra.py`, `src/llm_lab/kinds/codemetrics.py` (`asks_definitions=True`)
- Modify: `src/llm_lab/fixer.py` (`Request.follow_ups`·`asked`·`schema`, `make_request(follow_ups=)`, `propose_fixes`가 `request.schema`를 보냄)
- Test: `tests/test_followups.py` (새 파일)

**Interfaces:**
- Produces (`llm_lab.followups`): `MAX_ASKED_NAMES = 10`, `GUESS_RULE: str`, `ASK_RULE: str`, `JSON_RULE: str`, `follow_up_rules(rounds: int) -> str`, `with_follow_ups(prompt: str, rounds: int) -> str`, `with_need(schema: Mapping[str, Any]) -> dict[str, Any]`, `need_names(data: Any) -> list[str]`.
- Produces (`llm_lab.kinds.base.Kind`): `asks_definitions: bool = False`.
- Produces (`llm_lab.fixer`): `Request.follow_ups: int = 0`, `Request.asked: tuple[Definition, ...] = ()`, `Request.schema` (속성), `make_request(..., follow_ups: int = 0)`.
- 주의: `followups.py`는 `llm_lab.fixer`를 import하지 않는다(fixer가 followups를 import한다).

- [ ] **Step 1: 실패하는 테스트**

`tests/test_followups.py`:

```python
"""정의 되묻기: 지시문·답 형식·요청 (followups.py, make_request)."""

from dataclasses import replace

import pytest
from fake_llm import MODEL, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.fixer import FIX_SCHEMA_WITH_FILE, cache_key, make_request, propose_fixes
from llm_lab.followups import ASK_RULE, GUESS_RULE, need_names, with_follow_ups, with_need
from llm_lab.kinds import CODEMETRICS, MISRA, RTE
from llm_lab.sources import decode_source

SOURCE = decode_source("calc.c", CALC_C)


@pytest.mark.parametrize("kind", [RTE, MISRA, CODEMETRICS])
def test_every_kind_asks_and_gets_the_rule_before_the_json_rule(kind):
    assert kind.asks_definitions
    prompt = kind.prompt(None)
    assert GUESS_RULE in prompt

    text = with_follow_ups(prompt, 2)

    assert GUESS_RULE not in text and ASK_RULE in text
    assert 'put its exact name in "need", at most 10 names. You can ask 2 times in total.' in text
    assert text.index('"need"') < text.index("Answer with JSON only.")


def test_zero_rounds_keep_the_prompt_and_one_round_is_singular():
    prompt = RTE.prompt(None)
    assert with_follow_ups(prompt, 0) == prompt
    assert "You can ask 1 time in total." in with_follow_ups(prompt, 1)


def test_with_need_adds_a_required_list_and_leaves_the_original():
    schema = with_need(FIX_SCHEMA_WITH_FILE)

    assert schema["properties"]["need"] == {"type": "array", "items": {"type": "string"}}
    assert schema["required"][-1] == "need"
    assert "need" not in FIX_SCHEMA_WITH_FILE["properties"]
    assert "need" not in FIX_SCHEMA_WITH_FILE["required"]


def test_need_names_keeps_identifiers_once_and_at_most_ten():
    data = {"need": ["helper", " helper ", "a b", "x->y", 3, "_ok1", *[f"n{i}" for i in range(12)]]}

    assert need_names(data) == ["helper", "_ok1", *[f"n{i}" for i in range(8)]]
    assert need_names({"need": "helper"}) == []
    assert need_names({}) == []
    assert need_names([]) == []


def test_make_request_without_follow_ups_sends_todays_messages():
    plain = make_request(RTE, [finding()], SOURCE)
    zero = make_request(RTE, [finding()], SOURCE, follow_ups=0)

    assert zero.messages == plain.messages
    assert zero.follow_ups == 0 and zero.asked == ()
    assert zero.schema is RTE.answer_schema
    assert cache_key(MODEL, zero) == cache_key(MODEL, plain)


def test_make_request_with_follow_ups_changes_prompt_schema_and_key():
    plain = make_request(RTE, [finding()], SOURCE)
    asking = make_request(RTE, [finding()], SOURCE, follow_ups=2)

    assert asking.follow_ups == 2
    assert "need" in asking.schema["properties"]
    assert ASK_RULE in asking.messages[0]["content"]
    assert asking.messages[1] == plain.messages[1]
    assert cache_key(MODEL, asking) != cache_key(MODEL, plain)
    once = make_request(RTE, [finding()], SOURCE, follow_ups=1)
    assert cache_key(MODEL, asking) != cache_key(MODEL, once)


def test_kind_that_does_not_ask_ignores_follow_ups():
    kind = replace(RTE, asks_definitions=False)

    request = make_request(kind, [finding()], SOURCE, follow_ups=2)

    assert request.follow_ups == 0
    assert request.schema is RTE.answer_schema


def test_propose_fixes_sends_the_schema_with_need_when_asking():
    handler, sent = scripted(fix_answer())
    request = make_request(RTE, [finding()], SOURCE, follow_ups=2)

    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.decision == "fix"
    assert "need" in sent[0]["response_format"]["json_schema"]["schema"]["properties"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_followups.py -q`
Expected: FAIL (`llm_lab.followups` 없음)

- [ ] **Step 3: `followups.py` 만들기**

`src/llm_lab/followups.py`:

```python
"""LLM이 답의 need 칸으로 보이지 않는 정의를 되묻게 하는 규칙.

지시문에 되묻기 규칙을 붙이고, 답 형식에 need를 더하고, 답에서 이름을 꺼낸다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

MAX_ASKED_NAMES = 10  # 한 번에 받는 이름 수
NEED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 세 종류 지시문에 공통으로 있는 문장. 되묻기를 켜면 ASK_RULE로 바꾼다
GUESS_RULE = (
    "Do not guess definitions (macros, types, other functions) that are not shown. "
    "If they matter, say so in reason."
)
ASK_RULE = (
    'Do not guess definitions that are not shown; ask for them in "need". '
    "If a name is not in the project, treat it as external code whose behavior is unknown, "
    "and say so in reason."
)
JSON_RULE = "Answer with JSON only."  # 되묻기 규칙은 이 문장 앞에 붙인다


def follow_up_rules(rounds: int) -> str:
    """지시문에 붙이는 되묻기 규칙 문단."""
    times = "time" if rounds == 1 else "times"
    return (
        "If you need a definition that is not shown to decide (a function body, a macro, "
        f'a type or a variable), put its exact name in "need", at most {MAX_ASKED_NAMES} names. '
        f"You can ask {rounds} {times} in total. "
        'When "need" is not empty your findings are provisional: the definitions will be sent '
        "and you will answer again. Do not ask for names already shown. "
        'Leave "need" empty when the code shown is enough.\n'
    )


def with_follow_ups(prompt: str, rounds: int) -> str:
    """되묻기를 켠 지시문. rounds가 0이면 prompt 그대로."""
    if rounds <= 0:
        return prompt
    text = prompt.replace(GUESS_RULE, ASK_RULE)
    rules = follow_up_rules(rounds)
    at = text.find(JSON_RULE)
    return text + rules if at < 0 else text[:at] + rules + text[at:]


def with_need(schema: Mapping[str, Any]) -> dict[str, Any]:
    """답 형식에 필수 칸 need(이름 목록)를 더한 사본. 원래 형식은 그대로 둔다."""
    out = copy.deepcopy(dict(schema))
    out["properties"] = {
        **out["properties"],
        "need": {"type": "array", "items": {"type": "string"}},
    }
    out["required"] = [*out["required"], "need"]
    return out


def need_names(data: Any) -> list[str]:
    """답의 need에서 C 이름 모양인 것만, 한 번씩, 앞에서 MAX_ASKED_NAMES개."""
    raw = data.get("need") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        name = item.strip() if isinstance(item, str) else ""
        if NEED_NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names[:MAX_ASKED_NAMES]
```

- [ ] **Step 4: `Kind`에 칸 더하기**

`src/llm_lab/kinds/base.py`의 `sends_callers: bool = False` 아래에:

```python
    # 답의 need로 보이지 않는 정의를 되물을 수 있는지 (LLM_FOLLOW_UPS번까지)
    asks_definitions: bool = False
```

`src/llm_lab/kinds/rte.py`의 `RTE = Kind(...)`에서 `sends_callers=True,` 아래, `src/llm_lab/kinds/misra.py`의 `MISRA = Kind(...)`에서 `facts=facts,` 아래, `src/llm_lab/kinds/codemetrics.py`의 `CODEMETRICS = Kind(...)`에서 `auto_select=not_middle,` 아래에 각각:

```python
    asks_definitions=True,
```

- [ ] **Step 5: `Request`와 `make_request`**

`src/llm_lab/fixer.py`:
- 맨 위 import에 `from llm_lab.followups import with_follow_ups, with_need`를 더한다(`from llm_lab.csource import (` 블록 아래).
- `class Request`의 `callers: tuple[Definition, ...] = ()` 아래에:

```python
    follow_ups: int = 0  # 되물을 수 있는 횟수 (0이면 되묻지 않음, 지시문·답 형식도 예전 그대로)
    asked: tuple[Definition, ...] = ()  # 되묻기로 보낸 정의 (보낸 순서). 고칠 수 있는 범위의 마지막 순서

    @property
    def schema(self) -> Mapping[str, Any]:
        """보낼 답 형식. 되물을 수 있으면 need가 더해진다."""
        if self.follow_ups:
            return with_need(self.kind.answer_schema)
        return self.kind.answer_schema
```

- `make_request`의 인자 `files: Mapping[str, SourceFile] | None = None,` 뒤에 `follow_ups: int = 0,`를 더하고, 독스트링 첫 줄 아래에 `follow_ups: 종류가 되묻기를 쓰면 지시문에 규칙을 붙이고 답 형식에 need를 더한다.`를 적는다.
- `messages = [` 바로 앞에 `rounds = follow_ups if kind.asks_definitions else 0`, 시스템 메시지를 `{"role": "system", "content": with_follow_ups(kind.prompt(context), rounds)},`로 바꾸고, 반환을 다음으로 바꾼다:

```python
    return Request(
        kind,
        list(findings),
        source,
        first,
        last,
        context,
        messages,
        picked,
        editable,
        callers,
        follow_ups=rounds,
    )
```

- `propose_fixes` 안의 `_ask(client, model, messages, kind.answer_schema, transcript)`를 `_ask(client, model, messages, request.schema, transcript)`로 바꾼다.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_followups.py tests/test_fixer.py tests/test_kinds.py -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add src/llm_lab/followups.py src/llm_lab/kinds src/llm_lab/fixer.py tests/test_followups.py
git commit -m "feat: follow-up rule in prompts and need in the answer schema when LLM_FOLLOW_UPS > 0"
```

---

### Task 3: 프로젝트 색인과 이름 찾기

**Files:**
- Modify: `src/llm_lab/csource.py` (`_rank` → `definition_rank`, 공개)
- Modify: `src/llm_lab/followups.py`
- Test: `tests/test_followups.py`

**Interfaces:**
- Consumes: `csource.scan_project(root) -> (definitions, unreadable, texts)`, `csource.macro_target`, `csource.PER_NAME`, `csource.ALIAS_DEPTH`, `sources.decode_source`.
- Produces (`llm_lab.followups`):
  - `MAX_ASKED_DEFINITIONS = 20`, `MAX_ASKED_LINES = 600`
  - `ProjectIndex(root: Path, definitions: Mapping[str, tuple[Definition, ...]])`, `ProjectIndex.build(root: Path) -> ProjectIndex`, `ProjectIndex.source(name: str) -> SourceFile | None`
  - `IndexCache(build: Callable[[Path], ProjectIndex] = ProjectIndex.build, keep: int = 2)`, `IndexCache.get(job_id: int, root: str) -> ProjectIndex | None`
  - `Asked(definitions=(), missing=(), shown=(), later=(), unavailable=False, files={})` (frozen dataclass; `files: Mapping[str, SourceFile]`는 새로 고칠 수 있게 된 파일, Task 6의 worker가 채운다)
  - `find_asked(names: list[str], index: ProjectIndex | None, source_name: str, first: int, last: int, sent: Iterable[Definition]) -> Asked`
  - `csource.definition_rank(definition: Definition, source_name: str) -> int`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_followups.py` 맨 위 import에 `import threading`, `from pathlib import Path`, `from llm_lab import followups`, `from llm_lab.csource import Definition`을 더하고, `from llm_lab.followups import ...`에 `Asked, IndexCache, ProjectIndex, find_asked`를 더한다. 끝에:

```python
API = Definition("api_get", "macro", "inc/api.h", 3, 3, "#define api_get(x) impl_get(x)")
IMPL = Definition(
    "impl_get", "function", "gen/impl.c", 10, 13, "int impl_get(int x)\n{\n    return x;\n}"
)
HERE = Definition("divide", "function", "calc.c", 1, 4, "int divide(int a, int b)\n{\n}\n")
OTHER = Definition("other_fn", "function", "gen/o.c", 1, 3, "int other_fn(void)\n{\n}")


def index_of(*definitions: Definition) -> ProjectIndex:
    by_name: dict[str, list[Definition]] = {}
    for definition in definitions:
        by_name.setdefault(definition.name, []).append(definition)
    return ProjectIndex(Path("."), {name: tuple(found) for name, found in by_name.items()})


def test_find_asked_sends_new_definitions_and_follows_aliases():
    asked = find_asked(["api_get"], index_of(API, IMPL), "calc.c", 1, 4, ())

    assert asked.definitions == (API, IMPL)
    assert asked.missing == asked.shown == asked.later == ()


def test_find_asked_sorts_names_into_missing_and_shown():
    asked = find_asked(
        ["nowhere", "divide", "impl_get"], index_of(HERE, IMPL), "calc.c", 1, 4, (IMPL,)
    )

    assert asked.missing == ("nowhere",)
    assert asked.shown == ("divide", "impl_get")  # 보낸 코드 안, 이미 보낸 정의
    assert asked.definitions == ()


def test_alias_already_sent_still_brings_its_target():
    asked = find_asked(["api_get"], index_of(API, IMPL), "calc.c", 1, 4, (API,))

    assert asked.definitions == (IMPL,)
    assert asked.shown == ()


def test_find_asked_stops_at_the_definition_limit(monkeypatch):
    monkeypatch.setattr(followups, "MAX_ASKED_DEFINITIONS", 1)

    asked = find_asked(
        ["impl_get", "other_fn", "nowhere"], index_of(IMPL, OTHER), "calc.c", 1, 4, ()
    )

    assert asked.definitions == (IMPL,)
    assert asked.later == ("other_fn",)
    assert asked.missing == ("nowhere",)


def test_find_asked_stops_at_the_line_limit(monkeypatch):
    monkeypatch.setattr(followups, "MAX_ASKED_LINES", 3)

    asked = find_asked(["other_fn", "impl_get"], index_of(IMPL, OTHER), "calc.c", 1, 4, ())

    assert asked.definitions == (OTHER,)
    assert asked.later == ("impl_get",)


def test_no_index_means_the_names_cannot_be_looked_up():
    assert find_asked(["x"], None, "calc.c", 1, 4, ()) == Asked(unavailable=True)


def test_project_index_reads_the_folder_and_rereads_one_file(tmp_path):
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "impl.c").write_bytes(b"int impl_get(int x)\n{\n    return x;\n}\n")

    index = ProjectIndex.build(tmp_path)

    assert [d.file for d in index.definitions["impl_get"]] == ["gen/impl.c"]
    assert index.source("gen/impl.c").text.startswith("int impl_get")
    assert index.source("gen/none.c") is None


def test_index_cache_builds_each_job_once_and_keeps_two(tmp_path):
    built: list[Path] = []
    cache = IndexCache(build=lambda root: built.append(root) or ProjectIndex(root, {}))

    threads = [threading.Thread(target=cache.get, args=(1, str(tmp_path))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(built) == 1

    cache.get(2, str(tmp_path))
    cache.get(3, str(tmp_path))
    cache.get(1, str(tmp_path))  # 2개만 두므로 1은 다시 만든다
    assert len(built) == 4
    assert cache.get(9, str(tmp_path / "gone")) is None
    assert cache.get(8, "") is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_followups.py -q`
Expected: 새 테스트 FAIL (`ProjectIndex` 없음)

- [ ] **Step 3: `definition_rank` 공개**

`src/llm_lab/csource.py`에서 `def _rank(definition: Definition, source_name: str) -> int:`를 `def definition_rank(definition: Definition, source_name: str) -> int:`로 바꾸고, 독스트링 `"""정의 후보 순서: 검사 파일 0, 헤더 1, 그 밖 2."""`를 단다. `pick_definitions` 안의 `_rank(d, source_name)`도 `definition_rank(d, source_name)`로 바꾼다.

- [ ] **Step 4: 색인과 찾기 구현**

`src/llm_lab/followups.py`의 import를 다음으로 바꾼다:

```python
from __future__ import annotations

import copy
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_lab.csource import (
    ALIAS_DEPTH,
    PER_NAME,
    Definition,
    definition_rank,
    macro_target,
    scan_project,
)
from llm_lab.sources import DecodeError, SourceFile, decode_source
```

`MAX_ASKED_NAMES = 10` 아래에:

```python
MAX_ASKED_DEFINITIONS = 20  # 한 번에 싣는 정의 수
MAX_ASKED_LINES = 600  # 한 번에 싣는 정의 줄 수 합
```

파일 끝에:

```python
@dataclass(frozen=True)
class ProjectIndex:
    """소스 폴더 전체의 정의 (이름 → 정의 목록). 되물은 이름을 여기서 찾는다."""

    root: Path
    definitions: Mapping[str, tuple[Definition, ...]]

    @classmethod
    def build(cls, root: Path) -> ProjectIndex:
        found, _, _ = scan_project(root)
        by_name: dict[str, list[Definition]] = {}
        for definition in found:
            by_name.setdefault(definition.name, []).append(definition)
        return cls(root, {name: tuple(items) for name, items in by_name.items()})

    def source(self, name: str) -> SourceFile | None:
        """파일을 소스 폴더에서 다시 읽는다. 없거나 못 읽으면 None."""
        try:
            return decode_source(name, (self.root / name).read_bytes())
        except (DecodeError, OSError):
            return None


class IndexCache:
    """작업마다 색인을 한 번만 만든다. 최근 작업 keep개만 메모리에 둔다.

    작업자 스레드 여럿이 같은 작업을 처리하므로 잠금 안에서 만든다.
    """

    def __init__(
        self, build: Callable[[Path], ProjectIndex] = ProjectIndex.build, keep: int = 2
    ) -> None:
        self._build = build
        self._keep = keep
        self._lock = threading.Lock()
        self._items: OrderedDict[int, ProjectIndex | None] = OrderedDict()

    def get(self, job_id: int, root: str) -> ProjectIndex | None:
        """작업의 색인. 소스 폴더가 없거나 읽다 실패하면 None."""
        with self._lock:
            if job_id in self._items:
                self._items.move_to_end(job_id)
                return self._items[job_id]
            index = None
            if root and Path(root).is_dir():
                try:
                    index = self._build(Path(root))
                except OSError:
                    index = None
            self._items[job_id] = index
            while len(self._items) > self._keep:
                self._items.popitem(last=False)
            return index


@dataclass(frozen=True)
class Asked:
    """되묻기 한 번에 답할 것."""

    definitions: tuple[Definition, ...] = ()  # 보낼 정의 (보낼 순서)
    missing: tuple[str, ...] = ()  # 프로젝트에 정의가 없는 이름
    shown: tuple[str, ...] = ()  # 이미 보낸 이름
    later: tuple[str, ...] = ()  # 상한으로 이번에 못 보낸 이름
    unavailable: bool = False  # 색인을 만들 수 없어 찾지 못함
    files: Mapping[str, SourceFile] = field(default_factory=dict)  # 새로 고칠 수 있게 된 파일


class _Full(Exception):
    """이번 되묻기에 실을 수 있는 정의 수·줄 수를 넘음."""


def find_asked(
    names: list[str],
    index: ProjectIndex | None,
    source_name: str,
    first: int,
    last: int,
    sent: Iterable[Definition],
) -> Asked:
    """되물은 이름의 정의를 색인에서 고른다.

    이름마다 검사 파일 → 헤더 → 그 밖 순서로 PER_NAME개, 별칭 매크로는 가리키는 구현까지
    (ALIAS_DEPTH 단계). 보낸 코드 범위(source_name의 first~last) 안의 것과 sent에 있는 몸체는
    싣지 않는다. 새로 실은 것이 없으면 그 이름은 shown이다.
    """
    if index is None:
        return Asked(unavailable=True)
    placed = {(definition.file, definition.first) for definition in sent}
    picked: list[Definition] = []
    total = 0

    def place(name: str, depth: int) -> bool:
        """name의 정의를 싣는다. 새로 실은 것이 있으면 True. 상한을 넘으면 _Full."""
        nonlocal total
        added = False
        candidates = sorted(
            index.definitions.get(name, ()),
            key=lambda d: (definition_rank(d, source_name), d.file, d.first),
        )
        for definition in candidates[:PER_NAME]:
            key = (definition.file, definition.first)
            inside = (
                definition.file == source_name
                and first <= definition.first
                and definition.last <= last
            )
            if not inside and key not in placed:
                size = definition.text.count("\n") + 1
                if len(picked) >= MAX_ASKED_DEFINITIONS or total + size > MAX_ASKED_LINES:
                    raise _Full
                picked.append(definition)
                placed.add(key)
                total += size
                added = True
            # 별칭은 이미 보냈어도 가리키는 구현은 따라간다
            target = macro_target(definition)
            if target and depth < ALIAS_DEPTH:
                added = place(target, depth + 1) or added
        return added

    missing: list[str] = []
    shown: list[str] = []
    later: list[str] = []
    full = False
    for name in names:
        if name not in index.definitions:
            missing.append(name)
        elif full:
            later.append(name)
        else:
            try:
                if not place(name, 0):
                    shown.append(name)
            except _Full:
                full = True
                later.append(name)
    return Asked(tuple(picked), tuple(missing), tuple(shown), tuple(later))
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_followups.py tests/test_csource.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/llm_lab/csource.py src/llm_lab/followups.py tests/test_followups.py
git commit -m "feat: project index and lookup for names the LLM asks for"
```

---

### Task 4: fixer의 되묻기 대화

**Files:**
- Modify: `src/llm_lab/followups.py` (되묻기 메시지 문장 상수)
- Modify: `src/llm_lab/fixer.py` (`definitions_block` 나누기, `follow_up_message`, `Request.with_asked`, `_ranges`의 셋째 순서, `propose_fixes(ask=)`)
- Test: `tests/test_follow_up_loop.py` (새 파일)

**Interfaces:**
- Consumes: Task 2의 `Request.follow_ups`·`asked`·`schema`, `need_names`; Task 3의 `Asked`.
- Produces (`llm_lab.followups`): `ASKED_EDITABLE`, `ASKED_REFERENCE`, `MISSING_LINE`, `SHOWN_LINE`, `LATER_LINE`, `UNAVAILABLE_LINE`, `AGAIN_LINE`, `LAST_LINE` (문자열 상수, Task 7의 peek_logs도 쓴다).
- Produces (`llm_lab.fixer`): `Ask = Callable[[list[str], Request], Asked]`, `follow_up_message(asked: Asked, editable: Mapping[str, SourceFile] | None, rounds_left: int) -> str`, `Request.with_asked(asked: Asked) -> Request`, `propose_fixes(client, model, request, transcript=None, ask: Ask | None = None) -> list[FixResult]`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_follow_up_loop.py`:

```python
"""정의 되묻기 대화: propose_fixes가 need를 받아 다음 메시지를 보내고 다시 판단을 받는다."""

import json

from fake_llm import MODEL, completion, make_client, scripted
from misra_fixtures import misra_finding
from rte_fixtures import REASON, finding

from llm_lab.csource import Definition
from llm_lab.fixer import make_request, propose_fixes
from llm_lab.followups import (
    ASKED_EDITABLE,
    ASKED_REFERENCE,
    LAST_LINE,
    LATER_LINE,
    MISSING_LINE,
    SHOWN_LINE,
    UNAVAILABLE_LINE,
    Asked,
)
from llm_lab.kinds import MISRA, RTE
from llm_lab.sources import decode_source

USE = decode_source(
    "src/calc.c", b"int divide(int a, int b)\n{\n    return a / deep_scale(b);\n}\n"
)
DEEP = decode_source("lib/deep.c", b"int deep_scale(int v)\n{\n    return v - 1;\n}\n")
DEEP_DEF = Definition("deep_scale", "function", "lib/deep.c", 1, 4, DEEP.text.rstrip("\n"))
DEEP_FIX = {"file": "lib/deep.c", "original": "    return v - 1;", "replacement": "    return v + 1;"}


def reply(*findings, edits=None, need=()):
    body = {
        "findings": [
            {"number": number, "decision": decision, "reason": reason}
            for number, (decision, reason) in enumerate(findings, start=1)
        ],
        "edits": edits or [],
        "need": list(need),
    }
    return completion(json.dumps(body))


def asking(follow_ups=2):
    return make_request(RTE, [finding(line=3)], USE, None, (), {}, follow_ups=follow_ups)


def test_empty_need_answers_in_one_request():
    handler, sent = scripted(reply(("no_fix", REASON)))
    calls = []

    (result,) = propose_fixes(
        make_client(handler), MODEL, asking(), ask=lambda names, request: calls.append(names)
    )

    assert len(sent) == 1 and calls == []
    assert result.decision == "no_fix"


def test_asked_definition_is_sent_and_its_file_can_be_fixed():
    handler, sent = scripted(
        reply(("manual", REASON), need=["deep_scale"]),
        reply(("fix", REASON), edits=[DEEP_FIX]),
    )
    seen = []

    def ask(names, request):
        seen.append(names)
        return Asked(definitions=(DEEP_DEF,), files={"lib/deep.c": DEEP})

    (result,) = propose_fixes(make_client(handler), MODEL, asking(), ask=ask)

    assert seen == [["deep_scale"]]
    assert [m["role"] for m in sent[1]["messages"]] == ["system", "user", "assistant", "user"]
    follow_up = sent[1]["messages"][-1]["content"]
    assert follow_up.startswith(ASKED_EDITABLE)
    assert "// editable: lib/deep.c lines 1-4\n    1| int deep_scale(int v)" in follow_up
    assert follow_up.endswith("Answer again with the same JSON format. You can ask 1 more time.")
    assert result.decision == "fix"
    assert result.edits[0].file == "lib/deep.c"
    assert "+    return v + 1;" in result.diff


def test_names_not_found_are_called_external_and_the_last_round_says_no_more():
    handler, sent = scripted(
        reply(("manual", REASON), need=["ext_fn"]),
        reply(("manual", REASON), need=["ext_fn"]),
        reply(("manual", REASON), need=["ext_fn"]),  # 마지막 차례의 need는 무시한다
    )
    asked = Asked(missing=("ext_fn",), shown=("divide",), later=("big_fn",))

    (result,) = propose_fixes(
        make_client(handler), MODEL, asking(), ask=lambda names, request: asked
    )

    assert len(sent) == 3
    first = sent[1]["messages"][-1]["content"]
    last = sent[2]["messages"][-1]["content"]
    assert MISSING_LINE + "ext_fn." in first
    assert SHOWN_LINE + "divide." in first
    assert LATER_LINE + "big_fn." in first
    assert first.endswith("You can ask 1 more time.")
    assert last.endswith(LAST_LINE)
    assert result.decision == "manual"


def test_lookup_that_cannot_run_says_so():
    handler, sent = scripted(reply(("manual", REASON), need=["x"]), reply(("manual", REASON)))

    propose_fixes(
        make_client(handler),
        MODEL,
        asking(follow_ups=1),
        ask=lambda names, request: Asked(unavailable=True),
    )

    assert sent[1]["messages"][-1]["content"] == UNAVAILABLE_LINE + "\n\n" + LAST_LINE


def test_bad_json_while_asking_is_retried_once_in_that_round():
    handler, sent = scripted(
        completion("not json"),
        reply(("manual", REASON), need=["x"]),
        reply(("no_fix", REASON)),
    )

    (result,) = propose_fixes(
        make_client(handler),
        MODEL,
        asking(follow_ups=1),
        ask=lambda names, request: Asked(missing=("x",)),
    )

    assert len(sent) == 3
    assert sent[1]["messages"][-1]["content"].startswith("Your answer could not be used")
    assert result.decision == "no_fix"


def test_without_ask_need_is_ignored():
    handler, sent = scripted(reply(("no_fix", REASON), need=["x"]))

    (result,) = propose_fixes(make_client(handler), MODEL, asking())

    assert len(sent) == 1 and result.decision == "no_fix"


def test_misra_gets_asked_definitions_for_reference_only():
    handler, sent = scripted(reply(("manual", REASON), need=["deep_scale"]), reply(("manual", REASON)))
    request = make_request(MISRA, [misra_finding(line=3)], USE, None, (), {}, follow_ups=1)

    propose_fixes(
        make_client(handler),
        MODEL,
        request,
        ask=lambda names, request: Asked(definitions=(DEEP_DEF,), files={"lib/deep.c": DEEP}),
    )

    follow_up = sent[1]["messages"][-1]["content"]
    assert follow_up.startswith(ASKED_REFERENCE)
    assert "// lib/deep.c lines 1-4\nint deep_scale(int v)" in follow_up
    assert "    1|" not in follow_up
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_follow_up_loop.py -q`
Expected: FAIL (`ASKED_EDITABLE` 등 없음, `propose_fixes`에 `ask` 인자 없음)

- [ ] **Step 3: 메시지 문장 상수**

`src/llm_lab/followups.py`의 `JSON_RULE` 아래에:

```python
# 되묻기에 답하는 메시지의 문장 (fixer가 쓰고, scripts/peek_logs.py가 세는 데 쓴다)
ASKED_EDITABLE = (
    'Definitions you asked for. Those marked editable may be changed; set "file" to their path.'
)
ASKED_REFERENCE = "Definitions you asked for, for reference only."
MISSING_LINE = "Not in the project, so their behavior is unknown (external code): "
SHOWN_LINE = "Already shown above: "
LATER_LINE = "Not sent this time because of the size limit; ask again if you still need them: "
UNAVAILABLE_LINE = "Definitions cannot be looked up now."
AGAIN_LINE = "Answer again with the same JSON format. You can ask "
LAST_LINE = 'No more definitions can be sent. Answer with "need": [].'
```

- [ ] **Step 4: `definitions_block` 나누기와 되묻기 메시지**

`src/llm_lab/fixer.py`:
- import: `from collections.abc import ...`에 `Callable`이 없으면 더한다. `from llm_lab.followups import with_follow_ups, with_need`를 다음으로 바꾼다:

```python
from llm_lab.followups import (
    AGAIN_LINE,
    ASKED_EDITABLE,
    ASKED_REFERENCE,
    LAST_LINE,
    LATER_LINE,
    MISSING_LINE,
    SHOWN_LINE,
    UNAVAILABLE_LINE,
    Asked,
    need_names,
    with_follow_ups,
    with_need,
)
```

- `definitions_block`를 두 함수로 나눈다(보내는 글은 지금과 같아야 한다):

```python
def _definition_blocks(
    definitions: tuple[Definition, ...], editable: Mapping[str, SourceFile] | None
) -> tuple[list[str], bool]:
    """정의마다 코드 블록 하나, 그리고 고칠 수 있는 정의가 하나라도 있는지.

    editable에 원문이 있는 파일의 정의는 줄 번호를 붙여 고칠 수 있게, 나머지는 번호 없이 참고로.
    고칠 수 있는 정의가 하나도 없으면 참고 블록에 라벨을 달지 않는다 (예전과 같은 글).
    """
    editable = editable or {}
    labelled = any(definition.file in editable for definition in definitions)
    parts = []
    for definition in definitions:
        where = (
            f"line {definition.first}"
            if definition.first == definition.last
            else f"lines {definition.first}-{definition.last}"
        )
        source = editable.get(definition.file)
        if source is None:
            label = "reference only: " if labelled else ""
            parts.append(f"```c\n// {label}{definition.file} {where}\n{definition.text}\n```")
            continue
        last = _shown_last(definition)
        body = numbered(source.text, definition.first, last)
        if last < definition.last:
            body += f"\n... ({definition.last - last} more lines, not editable)"
        parts.append(f"```c\n// editable: {definition.file} {where}\n{body}\n```")
    return parts, labelled


def definitions_block(
    definitions: tuple[Definition, ...], editable: Mapping[str, SourceFile] | None = None
) -> str:
    """코드 뒤에 붙이는 다른 곳의 정의 절."""
    if not definitions:
        return ""
    parts, labelled = _definition_blocks(definitions, editable)
    if not labelled:
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


def follow_up_message(
    asked: Asked, editable: Mapping[str, SourceFile] | None, rounds_left: int
) -> str:
    """되묻기에 답하는 사용자 메시지. rounds_left는 이 메시지 뒤에 더 되물을 수 있는 횟수."""
    parts = []
    if asked.definitions:
        blocks, labelled = _definition_blocks(asked.definitions, editable)
        heading = ASKED_EDITABLE if labelled else ASKED_REFERENCE
        parts.append(heading + "\n\n" + "\n\n".join(blocks))
    if asked.unavailable:
        parts.append(UNAVAILABLE_LINE)
    for line, names in (
        (MISSING_LINE, asked.missing),
        (SHOWN_LINE, asked.shown),
        (LATER_LINE, asked.later),
    ):
        if names:
            parts.append(line + ", ".join(names) + ".")
    if rounds_left > 0:
        times = "time" if rounds_left == 1 else "times"
        parts.append(f"{AGAIN_LINE}{rounds_left} more {times}.")
    else:
        parts.append(LAST_LINE)
    return "\n\n".join(parts)
```

- [ ] **Step 5: `Request.with_asked`와 고칠 범위의 셋째 순서**

`class Request`의 `schema` 속성 아래에:

```python
    def with_asked(self, asked: Asked) -> Request:
        """되묻기로 보낸 정의와 새로 고칠 수 있게 된 파일을 더한 요청."""
        return replace(
            self,
            asked=(*self.asked, *asked.definitions),
            files={**self.files, **asked.files},
        )
```

`_ranges`를 다음으로 바꾼다:

```python
def _ranges(request: Request, target: str) -> list[list[tuple[int, int]]]:
    """target 파일에서 수정을 찾을 줄 범위를 먼저 볼 순서대로 묶은 것.

    검사 파일은 보낸 범위를 먼저 보고, 거기 없을 때만 고칠 수 있게 보낸 같은 파일의 정의를 본다.
    되묻기로 보낸 정의는 그다음이다.
    """
    tiers = [[(request.first, request.last)]] if target == request.source.name else []
    if target in request.files:
        tiers.append(
            [(d.first, _shown_last(d)) for d in request.definitions if d.file == target]
            + [(c.first, c.last) for c in request.callers if c.file == target]
        )
        asked = [(d.first, _shown_last(d)) for d in request.asked if d.file == target]
        if asked:
            tiers.append(asked)
    return tiers
```

- [ ] **Step 6: `propose_fixes`의 대화**

`propose_fixes` 바로 위에:

```python
# 되물은 이름 → 이번에 보낼 것. worker가 프로젝트 색인으로 찾고 작업에 남긴다
Ask = Callable[[list[str], Request], Asked]


def _need_of(content: str, finish_reason: str | None) -> list[str]:
    """임시 답의 need. 잘렸거나 JSON이 아니면 EditMismatch (그 차례에서 다시 묻는다)."""
    if finish_reason == "length":
        raise EditMismatch("응답이 출력 길이 제한으로 잘림", "truncated")
    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError:
        raise EditMismatch("응답이 JSON이 아님", "format") from None
    return need_names(data)
```

`propose_fixes`를 다음으로 바꾼다:

```python
def propose_fixes(
    client: OpenAI,
    model: str,
    request: Request,
    transcript: list[Exchange] | None = None,
    ask: Ask | None = None,
) -> list[FixResult]:
    """묶음 하나에 대한 행별 판단과 함께 쓰는 수정.

    쓸 수 없는 답이면 차례마다 한 번 다시 요청한다. ask를 주면 답의 need에 적힌 이름을 ask로
    찾아 request.follow_ups번까지 다음 메시지로 보내고 다시 판단하게 한다. 마지막 차례의 답은
    need가 있어도 최종이다. 돌려주는 목록은 request.findings와 같은 순서다.
    transcript를 주면 LLM과 주고받은 요청·응답을 순서대로 담는다.
    """
    kind = request.kind
    client = client.with_options(timeout=kind.timeout, max_retries=1)
    messages = list(request.messages)
    rounds_left = request.follow_ups if ask is not None else 0
    retry = True
    while True:
        content, finish_reason = _ask(client, model, messages, request.schema, transcript)
        try:
            need = _need_of(content, finish_reason) if rounds_left else []
            if not need:
                return _to_results(content, finish_reason, request)
        except EditMismatch as exc:
            if not retry:
                raise FixError(user_error(exc.summary, RETRY_ACTION, f"{exc} (2번 시도)")) from None
            retry = False
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your answer could not be used: {exc}. "
                    "Answer again with the same JSON format.",
                },
            ]
            continue
        assert ask is not None  # rounds_left는 ask가 있을 때만 0보다 크다
        asked = ask(need, request)
        request = request.with_asked(asked)
        rounds_left -= 1
        retry = True
        editable = request.files if kind.edits_definitions else None
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": follow_up_message(asked, editable, rounds_left)},
        ]
```

`MISMATCH_SUMMARIES["locate"]`를 쓰던 `problem, summary = ...` 줄은 없어진다(`EditMismatch`는 늘 `summary`를 가진다).

- [ ] **Step 7: 통과 확인**

Run: `uv run pytest tests/test_follow_up_loop.py tests/test_fixer.py tests/test_fixer_edits.py tests/test_followups.py -q`
Expected: PASS (예전 fixer 테스트도 그대로 통과해야 한다)

- [ ] **Step 8: 커밋**

```bash
git add src/llm_lab/followups.py src/llm_lab/fixer.py tests/test_follow_up_loop.py
git commit -m "feat: propose_fixes answers need with asked definitions and asks again"
```

---

### Task 5: 작업에 정의·파일 더하기와 재사용 값의 파일 목록

**Files:**
- Modify: `src/llm_lab/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `Store.add_definitions(job_id: int, definitions: Iterable[Definition]) -> None`, `Store.add_files(job_id: int, files: Iterable[SourceFile]) -> None` (이미 있는 파일은 그대로), `Store.cache_entry(key: str) -> tuple[list[FixResult], list[tuple[str, str]]] | None`, `Store.cache_put(key, results, files: Iterable[tuple[str, str]] = ())`. `cache_get`은 그대로 쓸 수 있다.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_store.py` 끝에(필요한 import: `from llm_lab.csource import Definition`, `from llm_lab.fixer import FixResult`, `from llm_lab.sources import decode_source`, `from llm_lab.store import NewRow, Store`, `from rte_fixtures import CALC_C, finding` — 이미 있는 것은 빼고 맨 위에 더한다):

```python
def test_add_files_and_definitions_keep_what_the_job_already_has(tmp_path):
    store = Store(tmp_path / "fixer.db")
    source = decode_source("calc.c", CALC_C)
    job = store.create_job(
        "x.xlsx", "RTE_Result", 0, [source], [NewRow(finding(), "calc.c", "pending")]
    )
    changed = decode_source("calc.c", b"int other;\n")
    extra = decode_source("lib/deep.c", b"int deep(void);\n")

    store.add_files(job, [changed, extra])
    deep = Definition("deep", "function", "lib/deep.c", 1, 1, "int deep(void);")
    store.add_definitions(job, [deep, deep])

    assert store.sources(job)["calc.c"].text == source.text
    assert store.sources(job)["lib/deep.c"].text == extra.text
    assert store.definitions(job) == [deep]


def test_cache_keeps_the_files_an_answer_relied_on(tmp_path):
    store = Store(tmp_path / "fixer.db")
    result = FixResult("no_fix", "이유", (), "")

    store.cache_put("plain", [result])
    store.cache_put("asked", [result], files=[("lib/deep.c", "abc")])

    assert store.cache_entry("plain") == ([result], [])
    assert store.cache_entry("asked") == ([result], [("lib/deep.c", "abc")])
    assert store.cache_get("asked") == [result]
    assert store.cache_entry("none") is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL (`add_files` 없음)

- [ ] **Step 3: 구현**

`src/llm_lab/store.py`의 `definitions` 메서드 아래에:

```python
    def add_definitions(self, job_id: int, definitions: Iterable[Definition]) -> None:
        """되묻기로 보낸 정의를 작업에 더한다. 이미 있는 정의는 그대로."""
        with self._write() as db:
            db.executemany(
                "INSERT OR IGNORE INTO definitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(job_id, d.name, d.kind, d.file, d.first, d.last, d.text) for d in definitions],
            )

    def add_files(self, job_id: int, files: Iterable[SourceFile]) -> None:
        """작업에 없는 파일만 더한다. 있는 파일은 분석 당시 원문을 지킨다."""
        with self._write() as db:
            db.executemany(
                "INSERT OR IGNORE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (job_id, f.name, f.text, f.encoding, f.newline, int(f.bom), f.sha256)
                    for f in files
                ],
            )
```

`cache_get`·`cache_put`을 다음으로 바꾼다:

```python
    def cache_entry(self, key: str) -> tuple[list[FixResult], list[tuple[str, str]]] | None:
        """(행별 결과, 되묻기로 고칠 수 있게 보낸 파일의 (이름, sha256) 목록).

        예전 값(결과 목록만)은 파일 목록이 빈 것으로 읽는다. 묶음 처리 전의 값(결과 하나)은 없음.
        """
        with self._db() as db:
            found = db.execute("SELECT result FROM cache WHERE key = ?", (key,)).fetchone()
        data = json.loads(found["result"]) if found else None
        files: list[tuple[str, str]] = []
        if isinstance(data, dict):
            files = [
                (item["name"], item["sha256"])
                for item in data.get("files", [])
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("sha256"), str)
            ]
            data = data.get("results")
        if not isinstance(data, list):
            return None
        return [FixResult.from_dict(item) for item in data], files

    def cache_get(self, key: str) -> list[FixResult] | None:
        """묶음의 행별 결과 (행 순서대로)."""
        entry = self.cache_entry(key)
        return entry[0] if entry else None

    def cache_put(
        self, key: str, results: list[FixResult], files: Iterable[tuple[str, str]] = ()
    ) -> None:
        """결과를 저장한다. files가 있으면 결과와 함께 (예전 서버는 이 값을 없는 것으로 본다)."""
        items = [asdict(result) for result in results]
        listed = [{"name": name, "sha256": sha256} for name, sha256 in files]
        data: object = {"results": items, "files": listed} if listed else items
        text = json.dumps(data, ensure_ascii=False)
        with self._write() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, text, _now()))
```

`Iterable`이 import되어 있지 않으면 `from collections.abc import Iterable`을 더한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_store.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/llm_lab/store.py tests/test_store.py
git commit -m "feat: store can add files and definitions to a job and cache the files an answer used"
```

---

### Task 6: worker 연결

**Files:**
- Modify: `src/llm_lab/worker.py`
- Modify: `src/llm_lab/web.py` (`Worker(..., follow_ups=settings.follow_ups)`)
- Test: `tests/test_worker_follow_ups.py` (새 파일), `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 `Settings.follow_ups`; Task 2 `make_request(follow_ups=)`; Task 3 `IndexCache`, `find_asked`, `Asked`; Task 4 `propose_fixes(ask=)`, `Ask`; Task 5 `cache_entry`, `cache_put(files=)`, `add_files`, `add_definitions`.
- Produces: `Worker(store, client, model, submit, log_dir=None, follow_ups: int = 0, indexes: IndexCache | None = None)`. 대화 기록의 결과 줄 맨 앞에 `정의 되묻기 N번`(N ≥ 1일 때).

- [ ] **Step 1: 실패하는 테스트**

`tests/test_worker_follow_ups.py`:

```python
"""worker의 정의 되묻기: 색인에서 찾아 보내고, 보낸 것을 작업에 남기고, 재사용 때 파일을 되살린다."""

import json
from pathlib import Path

from fake_llm import MODEL, completion, make_client, scripted
from rte_fixtures import REASON, finding

from llm_lab.csource import Definition
from llm_lab.followups import IndexCache, ProjectIndex
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import Worker

USE_TEXT = b"int divide(int a, int b)\n{\n    return a / deep_scale(b);\n}\n"
DEEP_TEXT = b"int deep_scale(int v)\n{\n    return v - 1;\n}\n"
DEEP_FIX = {"file": "lib/deep.c", "original": "    return v - 1;", "replacement": "    return v + 1;"}


def run_now(task):
    task()


def reply(decision, edits=None, need=()):
    body = {
        "findings": [{"number": 1, "decision": decision, "reason": REASON}],
        "edits": edits or [],
        "need": list(need),
    }
    return completion(json.dumps(body))


def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "src" / "calc.c").write_bytes(USE_TEXT)
    (root / "lib" / "deep.c").write_bytes(DEEP_TEXT)
    return root


def new_job(store: Store, root: Path) -> int:
    source = decode_source("src/calc.c", USE_TEXT)
    return store.create_job(
        "rte.xlsx",
        "RTE_Result",
        0,
        [source],
        [NewRow(finding(line=3), "src/calc.c", "pending")],
        source_root=str(root),
        kind="rte",
    )


def test_need_is_answered_from_the_project_and_what_was_sent_stays_in_the_job(tmp_path):
    root = project(tmp_path)
    handler, sent = scripted(reply("manual", need=["deep_scale"]), reply("fix", [DEEP_FIX]))
    store = Store(tmp_path / "fixer.db")
    worker = Worker(
        store, make_client(handler), MODEL, run_now, log_dir=tmp_path / "logs", follow_ups=2
    )
    job_id = new_job(store, root)

    worker.start(job_id)

    (row,) = store.rows(job_id)
    assert row.state == "done" and row.result.decision == "fix"
    assert row.result.edits[0].file == "lib/deep.c"
    assert "// editable: lib/deep.c lines 1-4" in sent[1]["messages"][-1]["content"]
    assert "lib/deep.c" in store.sources(job_id)
    assert [d.name for d in store.definitions(job_id)] == ["deep_scale"]
    text = next((tmp_path / "logs").glob("*.md")).read_text(encoding="utf-8")
    assert "- 결과: 정의 되묻기 1번" in text


def test_reused_answer_brings_its_files_while_they_are_unchanged(tmp_path):
    root = project(tmp_path)
    handler, sent = scripted(
        reply("manual", need=["deep_scale"]), reply("fix", [DEEP_FIX]), reply("no_fix")
    )
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now, follow_ups=2)
    worker.start(new_job(store, root))

    second = new_job(store, root)
    worker.start(second)
    assert len(sent) == 2  # 재사용: LLM을 부르지 않았다
    assert "lib/deep.c" in store.sources(second)
    assert store.rows(second)[0].result.decision == "fix"

    (root / "lib" / "deep.c").write_bytes(DEEP_TEXT.replace(b"v - 1", b"v - 2"))
    third = new_job(store, root)
    worker.start(third)
    assert len(sent) == 3  # 파일이 바뀌어 다시 물었다
    assert store.rows(third)[0].result.decision == "no_fix"


def test_file_that_cannot_be_read_again_is_sent_for_reference_only(tmp_path):
    # 색인에는 있는데 파일을 다시 읽지 못한다 (그사이 지워짐 등)
    root = project(tmp_path)
    ghost = Definition("ghost_fn", "function", "lib/ghost.c", 1, 3, "int ghost_fn(void)\n{\n}")
    indexes = IndexCache(build=lambda path: ProjectIndex(path, {"ghost_fn": (ghost,)}))
    handler, sent = scripted(reply("manual", need=["ghost_fn"]), reply("manual"))
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now, follow_ups=1, indexes=indexes)
    job_id = new_job(store, root)

    worker.start(job_id)

    follow_up = sent[1]["messages"][-1]["content"]
    assert "// lib/ghost.c lines 1-3\nint ghost_fn(void)" in follow_up
    assert "lib/ghost.c" not in store.sources(job_id)
    assert store.rows(job_id)[0].state == "done"


def test_no_follow_ups_sends_the_old_schema(tmp_path):
    root = project(tmp_path)
    handler, sent = scripted(reply("no_fix"))
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now)

    worker.start(new_job(store, root))

    assert "need" not in sent[0]["response_format"]["json_schema"]["schema"]["properties"]
```

`tests/test_web.py`의 `start_main`을 쓰는 테스트들 근처에:

```python
def test_main_passes_llm_follow_ups_to_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FOLLOW_UPS", "1")

    _, workers, _ = start_main(tmp_path, monkeypatch, ["--no-browser"])

    assert workers[0]["follow_ups"] == 1
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_worker_follow_ups.py tests/test_web.py -q`
Expected: FAIL (`Worker`에 `follow_ups` 인자 없음)

- [ ] **Step 3: worker 구현**

`src/llm_lab/worker.py`:
- import에 `from dataclasses import replace`, `from llm_lab.followups import Asked, IndexCache, find_asked`, `from llm_lab.sources import DecodeError, SourceFile, decode_source`를 더하고, `from llm_lab.fixer import (...)`에 `Ask`, `Request`를 더한다.
- `__init__`에 인자 `follow_ups: int = 0`, `indexes: IndexCache | None = None`를 `log_dir` 뒤에 더하고:

```python
        self.follow_ups = follow_ups  # 묶음마다 정의를 되물을 수 있는 횟수
        self.indexes = indexes or IndexCache()  # 되묻기 때 찾을 작업별 프로젝트 색인
```

- `_process`의 `exchanges: list[Exchange] = []` 아래에 `rounds: list[Asked] = []`를 두고, `try:` 안을 다음으로 바꾼다:

```python
            request = make_request(
                kind,
                findings,
                source,
                context,
                self.store.definitions(job_id),
                self.store.sources(job_id),
                follow_ups=self.follow_ups,
            )
            key = cache_key(self.model, request)
            entry = self.store.cache_entry(key)
            results = None
            if entry is not None and self._restore(job_id, job.source_root, entry[1]):
                results = entry[0]
            reused = results is not None
            if results is None:
                used: dict[str, str] = {}  # 되묻기로 고칠 수 있게 보낸 파일 → sha256
                ask = self._asker(job_id, job.source_root, rounds, used)
                results = propose_fixes(self.client, self.model, request, exchanges, ask=ask)
                self.store.cache_put(key, results, files=sorted(used.items()))
```

- `else:` 블록에서 `outcomes`를 만든 뒤(`if len(rows) > 1:` 블록 다음), `self._save_log(...)` 앞에:

```python
            if rounds:
                outcomes = [f"정의 되묻기 {len(rounds)}번", *outcomes]
```

- 클래스에 두 메서드를 더한다(`_save_log` 위):

```python
    def _asker(self, job_id: int, root: str, rounds: list[Asked], used: dict[str, str]) -> Ask:
        """되물은 이름을 작업의 프로젝트 색인에서 찾는 함수.

        보낸 정의는 작업에 더한다. 고칠 수 있는 종류면 그 정의의 파일도 작업에 더하고(작업에 없으면
        소스 폴더에서 다시 읽는다), 다시 읽지 못한 파일의 정의는 참고만으로 보낸다.
        """

        def ask(names: list[str], request: Request) -> Asked:
            index = self.indexes.get(job_id, root)
            asked = find_asked(
                names,
                index,
                request.source.name,
                request.first,
                request.last,
                (*request.definitions, *request.asked),
            )
            files: dict[str, SourceFile] = {}
            if request.kind.edits_definitions and index is not None:
                stored = self.store.sources(job_id)
                for name in dict.fromkeys(d.file for d in asked.definitions):
                    if name in request.files:
                        continue
                    if name == request.source.name:
                        files[name] = request.source
                        continue
                    found = stored.get(name) or index.source(name)
                    if found is not None:
                        files[name] = found
                        used[name] = found.sha256
                self.store.add_files(job_id, files.values())
            self.store.add_definitions(job_id, asked.definitions)
            asked = replace(asked, files=files)
            rounds.append(asked)
            return asked

        return ask

    def _restore(self, job_id: int, root: str, files: list[tuple[str, str]]) -> bool:
        """저장된 답이 기댄 파일을 작업에 둔다. 그 파일이 그때와 다르거나 없으면 False."""
        stored = self.store.sources(job_id)
        missing: list[SourceFile] = []
        for name, sha256 in files:
            have = stored.get(name)
            if have is None and root:
                try:
                    have = decode_source(name, (Path(root) / name).read_bytes())
                except (DecodeError, OSError):
                    have = None
                if have is not None:
                    missing.append(have)
            if have is None or have.sha256 != sha256:
                return False
        self.store.add_files(job_id, missing)
        return True
```

- [ ] **Step 4: web 연결**

`src/llm_lab/web.py`의 `worker = Worker(...)`를 다음으로 바꾼다:

```python
    worker = Worker(
        store,
        get_client(settings),
        settings.model,
        submit,
        log_dir=data_dir / LOG_DIR_NAME,
        follow_ups=settings.follow_ups,
    )
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_worker_follow_ups.py tests/test_worker.py tests/test_web.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/llm_lab/worker.py src/llm_lab/web.py tests/test_worker_follow_ups.py tests/test_web.py
git commit -m "feat: worker looks up asked names in the project and keeps what it sent with the job"
```

---

### Task 7: peek_logs의 되묻기 셈

**Files:**
- Modify: `scripts/peek_logs.py`
- Modify: `docs/commands.md` (peek_logs 설명)
- Test: `tests/test_peek_logs.py`

**Interfaces:**
- Consumes: Task 4의 문장 상수(`AGAIN_LINE`, `LAST_LINE`, `MISSING_LINE`, `SHOWN_LINE`, `LATER_LINE`, `UNAVAILABLE_LINE`), Task 2의 `need_names`.
- Produces: `follow_up_counts(folder: Path, job: int) -> dict[str, int]` (키: `groups`, `round_1`, `round_2`, `round_3`, `asked`, `sent`, `missing`, `shown`, `later`, `unavailable`, `changed`). 화면에서 "직접 수정 행이 받은 것" 절과 `manual_counts`·`LABELS`를 뺀다.

- [ ] **Step 1: 실패하는 테스트와 뺄 단언**

`tests/test_peek_logs.py`:
- `test_counts_manual_rows_by_what_was_sent`에서 `assert "고칠 수 있는 정의가 붙음" in screen`과 `counts = dict(peek.manual_counts(...))`부터 그 `assert counts == {...}` 블록 끝까지를 지운다(나머지 단언은 둔다).
- 끝에 더한다:

```python
def test_counts_follow_ups_without_showing_names(tmp_path):
    from llm_lab.followups import ASKED_EDITABLE, LAST_LINE, MISSING_LINE, SHOWN_LINE

    provisional = json.dumps(
        {
            "findings": [{"number": 1, "decision": "manual", "reason": "secret why"}],
            "edits": [],
            "need": ["secret_a", "secret_b", "secret_c"],
        }
    )
    follow_up = (
        ASKED_EDITABLE
        + "\n\n```c\n// editable: gen/secret.c lines 1-2\n    1| int secret_a;\n```\n\n"
        + MISSING_LINE
        + "secret_b.\n\n"
        + SHOWN_LINE
        + "secret_c.\n\n"
        + LAST_LINE
    )
    exchange = Exchange(
        [
            {"role": "system", "content": "secret system"},
            {"role": "user", "content": HEAD + CODE},
            {"role": "assistant", "content": provisional},
            {"role": "user", "content": follow_up},
        ],
        raw={"secret": "raw"},
        content=answer("fix"),
        finish_reason="stop",
    )
    text = render_log(
        job_id=4,
        kind="RTE",
        findings=[finding(row=2, file=r"C:\secret_dir\secret_file.c")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[exchange],
        outcomes=["정의 되묻기 1번", "secret outcome"],
    )
    write_log(tmp_path, 4, 2, text)
    log(tmp_path, 4, 3, HEAD + CODE, answer("no_fix"))

    counts = peek.follow_up_counts(tmp_path, 4)

    assert counts == {
        "groups": 1,
        "round_1": 1,
        "round_2": 0,
        "round_3": 0,
        "asked": 3,
        "sent": 1,
        "missing": 1,
        "shown": 1,
        "later": 0,
        "unavailable": 0,
        "changed": 1,
    }
    screen = "\n".join(peek.summarize(tmp_path, job=4))
    assert "되묻기: 되물은 묶음 1 / 2 (1번 1, 2번 0, 3번 0)" in screen
    assert "요청한 이름 3: 보냄 1, 프로젝트에 없음 1, 이미 보냄 1, 상한으로 못 보냄 0" in screen
    assert "되묻기 뒤 판단이 바뀐 행: 1" in screen
    assert "판단 행: 수정 1 / 수정 불필요 1 / 직접 수정 0" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_peek_logs.py -q`
Expected: 새 테스트 FAIL (`follow_up_counts` 없음)

- [ ] **Step 3: 구현**

`scripts/peek_logs.py`:
- import에 `from llm_lab.followups import AGAIN_LINE, LAST_LINE, LATER_LINE, MISSING_LINE, SHOWN_LINE, UNAVAILABLE_LINE, need_names`를 더한다(한 줄이 100자를 넘으면 괄호로 나눈다).
- `manual_counts` 함수와 `LABELS` 딕셔너리를 지운다.
- `REUSED = ...` 줄 아래에:

```python
TALK_HEAD = re.compile(r"^### (?:보낸 내용: (\w+)|받은 내용)[^\n]*\n", re.M)


def _conversation(text: str) -> list[tuple[str, str]]:
    """기록 파일의 마지막 요청에서 (역할, 내용) 차례. 받은 답은 assistant로 끝에 붙는다."""
    last = text.split("\n## 요청 ")[-1]
    found = []
    for match in TALK_HEAD.finditer(last):
        content = _first_fence(last[match.end() :])
        if content is not None:
            found.append((match.group(1) or "assistant", content))
    return found


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _listed(message: str, prefix: str) -> int:
    """되묻기 메시지에서 prefix로 시작하는 줄의 이름 수."""
    for line in message.splitlines():
        if line.startswith(prefix):
            return len([name for name in line[len(prefix) :].rstrip(".").split(", ") if name])
    return 0


def _decided(answer: dict) -> dict[object, object]:
    items = answer.get("findings") if isinstance(answer.get("findings"), list) else []
    return {item.get("number"): item.get("decision") for item in items if isinstance(item, dict)}


def follow_up_counts(folder: Path, job: int) -> dict[str, int]:
    """작업의 되묻기를 센다: 되물은 묶음, 횟수별 묶음, 요청한 이름의 처리, 판단이 바뀐 행."""
    counts: Counter[str] = Counter()
    for path in sorted(folder.glob(f"*_job{job}_row*.md")):
        if not LOG_NAME.search(path.name):
            continue
        talk = _conversation(path.read_text(encoding="utf-8", errors="replace"))
        rounds = 0
        first_answer: dict | None = None
        for index, (role, content) in enumerate(talk):
            if role != "user" or not (AGAIN_LINE in content or LAST_LINE in content):
                continue
            rounds += 1
            previous = _json(talk[index - 1][1]) if index else {}
            first_answer = previous if first_answer is None else first_answer
            names = len(need_names(previous))
            counts["asked"] += names
            counts["missing"] += _listed(content, MISSING_LINE)
            counts["shown"] += _listed(content, SHOWN_LINE)
            counts["later"] += _listed(content, LATER_LINE)
            if UNAVAILABLE_LINE in content:
                counts["unavailable"] += names
        if not rounds:
            continue
        counts["groups"] += 1
        counts[f"round_{min(rounds, 3)}"] += 1
        before = _decided(first_answer or {})
        after = _decided(_json(talk[-1][1]))
        counts["changed"] += sum(1 for number, decision in after.items() if before.get(number) != decision)
    counts["sent"] = max(
        0,
        counts["asked"] - counts["missing"] - counts["shown"] - counts["later"] - counts["unavailable"],
    )
    keys = ("groups", "round_1", "round_2", "round_3", "asked", "sent", "missing", "shown")
    keys += ("later", "unavailable", "changed")
    return {key: counts[key] for key in keys}
```

(100자를 넘는 줄은 ruff format이 나눈다. `ruff check`의 E501이 남으면 손으로 나눈다.)

- `summarize`에서 `lines = [ ... ]`의 `f"정의가 상한(...)..."` 줄 다음 `""` 앞에 되묻기 세 줄을 넣고, "직접 수정 행이 받은 것" 제목 줄과 그 아래 `for key, count in manual_counts(...)` 반복을 지운다. 결과는 이렇게 된다:

```python
    follow = follow_up_counts(folder, job)
    lines = [
        f"작업 {job}: 묶음 {len(logs)}개 (마지막 요청 기준), 저장된 답 재사용 묶음 {reused}개",
        "판단 행: " + " / ".join(f"{label} {decided[key]}" for key, label in DECISIONS),
        f"정의가 상한({MAX_DEFINITIONS}개)까지 붙은 묶음: {capped} / {len(logs)}, "
        f"직접 수정이 나온 묶음: {manual_groups}",
        f"되묻기: 되물은 묶음 {follow['groups']} / {len(logs)} "
        f"(1번 {follow['round_1']}, 2번 {follow['round_2']}, 3번 {follow['round_3']})",
        f"  요청한 이름 {follow['asked']}: 보냄 {follow['sent']}, "
        f"프로젝트에 없음 {follow['missing']}, 이미 보냄 {follow['shown']}, "
        f"상한으로 못 보냄 {follow['later']}, 찾을 수 없었음 {follow['unavailable']}",
        f"  되묻기 뒤 판단이 바뀐 행: {follow['changed']}",
    ]
    lines += ["", "직접 수정 이유가 짚은 이름 (한 행이 여러 줄에 들 수 있음):"]
```

  (`pattern` 인자는 `other_file_fixes`가 계속 쓴다.)
- 맨 위 독스트링 첫 문단에 "되묻기(LLM이 정의를 더 달라고 한 것)도 센다."를 더한다.

`docs/commands.md`의 peek_logs 행 설명 끝에 `, 정의 되묻기 수`를 더한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_peek_logs.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/peek_logs.py docs/commands.md tests/test_peek_logs.py
git commit -m "feat: peek_logs counts follow-ups and drops the settled attached-definitions block"
```

---

### Task 8: 문서와 현장 할 일

**Files:**
- Modify: `README.md` (기능 설명 글머리)
- Modify: `docs/company-checklist.md` (선택 설정 목록)
- Modify: `docs/superpowers/specs/2026-10-14-definition-follow-ups-design.md` (상태 줄)
- Modify: `todo.md`

- [ ] **Step 1: README 기능 설명**

`README.md`에서 "요청마다 40개·1500줄까지, 함수 구현을 타입·상수보다 먼저"가 든 글머리 바로 아래에 같은 들여쓰기로:

```markdown
- 정의 되묻기: LLM이 판단에 필요한데 안 보이는 함수 구현·매크로·타입을 답의 `need`에 적으면, 서버가 프로젝트에서 찾아
  보내고 다시 판단하게 합니다(묶음마다 `LLM_FOLLOW_UPS`번까지, 기본 2). 프로젝트에 없는 이름은 외부 코드라고 알려 줍니다.
  RTE는 받은 정의도 고칠 수 있고, MISRA·CodeMetrics는 참고로만 봅니다.
```

- [ ] **Step 2: 체크리스트와 스펙 상태**

`docs/company-checklist.md`의 선택 항목 줄 `` `LLM_WORKERS` ``)를 `` `LLM_WORKERS`, `LLM_FOLLOW_UPS` ``)로 바꾼다. 스펙의 `- 상태: 설계 검토 중`을 `- 상태: 구현됨 (2026-10-14)`로 바꾼다.

- [ ] **Step 3: todo.md**

`todo.md` 끝에 다음 번호의 절을 더한다(지금 마지막 절이 4면 5):

````markdown
## 5. 정의 되묻기로 RTE 다시 돌리기

LLM이 안 보이는 정의를 되물어 받아 보게 바꿨습니다. 직접 수정 41행이 줄어드는지 봅니다.

```bash
git pull
```

서버를 끄고(Ctrl+C) 같은 `--profile`로 다시 켭니다. `.env`의 `LLM_FOLLOW_UPS`는 비워 둡니다(기본 2).
같은 엑셀과 같은 프로젝트 최상위 폴더로, 처리 방식은 기본값(가드 허용) 그대로 "분석 시작"을 누릅니다.
끝나면 `b` 자리에 `--profile` 이름을 넣어 실행합니다.

```bash
uv run python scripts/peek_logs.py rte --profile b
```

알려줄 것:

- 화면 캡처 한 장 (숫자와 Polyspace 검사 이름만 나옵니다)
- "분석 시작"을 누른 뒤 결과 화면이 다 뜨기까지 걸린 시간 (지난번과 비교)
````

- [ ] **Step 4: 전체 검사**

Run: `uv run ruff format src tests scripts ; uv run ruff check . ; uv run pytest -q`
Expected: ruff 통과, 모든 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
git add README.md docs/company-checklist.md docs/superpowers/specs/2026-10-14-definition-follow-ups-design.md todo.md
git commit -m "docs: definition follow-ups in README, checklist and todo"
```
