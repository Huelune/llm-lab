"""scripts/peek_logs.py: 대화 기록에서 직접 수정 행을 개수로만 센다 (회사 정보는 안 찍음)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from rte_fixtures import finding

from llm_lab.fixer import Exchange
from llm_lab.transcript import render_log, write_log

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peek_logs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peek_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


peek = _load()

HEAD = "Polyspace Code Prover results in one C function:\n\nFinding 1:\n- File: secret_file.c\n"
CODE = "Code (lines 1-4 of secret_file.c):\n```c\n    1| int secret_func(void)\n```"
EDITABLE = (
    "\n\nDefinitions from other places. Those marked editable may be changed.\n\n"
    "```c\n// editable: gen/Rte_secret.c lines 1-4\n    1| int secret_api(void)\n```"
)
CLIPPED = (
    "\n\n```c\n// editable: gen/Rte_secret_big.c lines 1-200\n    1| int secret_big(void)"
    "\n... (120 more lines, not editable)\n```"
)
CALLER = (
    "\n\nCallers of secret_func(), for the values that reach its parameters.\n\n"
    "```c\n// editable caller: app/secret_main.c lines 1-5 (in secret_caller())\n"
    "    1| void secret_caller(void)\n```"
)
REFERENCE = (
    "\n\nDefinitions from other places, for reference only.\n\n"
    "```c\n// inc/secret.h line 3\n#define SECRET_LIMIT 4\n```"
)


def answer(decision: str, edits: list[dict] | None = None, reason: str = "secret reason") -> str:
    return json.dumps(
        {
            "findings": [{"number": 1, "decision": decision, "reason": reason}],
            "edits": edits or [],
        }
    )


def log(folder: Path, job: int, row: int, user: str, content: str) -> None:
    exchange = Exchange(
        [{"role": "system", "content": "secret system"}, {"role": "user", "content": user}],
        raw={"secret": "raw"},
        content=content,
        finish_reason="stop",
    )
    text = render_log(
        job_id=job,
        kind="RTE",
        findings=[finding(row=row, file=r"C:\secret_dir\secret_file.c", function="secret_func")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[exchange],
        outcomes=["secret outcome"],
    )
    write_log(folder, job, row, text)


def sample(folder: Path) -> None:
    log(folder, 3, 2, HEAD + CODE + EDITABLE, answer("manual"))
    log(folder, 3, 3, HEAD + CODE + EDITABLE + CLIPPED, answer("manual"))
    log(folder, 3, 4, HEAD + CODE + CALLER, answer("manual"))
    log(folder, 3, 5, HEAD + CODE + REFERENCE, answer("manual"))
    log(folder, 3, 6, HEAD + CODE, answer("manual"))
    other = {"file": "gen/Rte_secret.c", "original": "a", "replacement": "b"}
    log(folder, 3, 7, HEAD + CODE + EDITABLE, answer("fix", [other]))
    log(folder, 3, 8, HEAD + CODE, answer("no_fix"))
    log(folder, 2, 2, HEAD + CODE, answer("manual"))  # 예전 작업은 세지 않는다


def test_counts_manual_rows_by_what_was_sent(tmp_path):
    sample(tmp_path)

    screen = "\n".join(peek.summarize(tmp_path, pattern="rte"))

    assert "작업 3: 묶음 7개" in screen
    assert "수정 1 / 수정 불필요 1 / 직접 수정 5" in screen
    assert "고칠 수 있는 정의가 붙음" in screen
    counts = dict(peek.manual_counts(peek.read_logs(tmp_path, 3), pattern="rte"))
    assert counts == {
        "editable": 2,
        "editable_pattern": 2,
        "caller": 1,
        "reference_only": 1,
        "none": 1,
        "clipped": 1,
        "capped": 0,
    }
    assert peek.other_file_fixes(peek.read_logs(tmp_path, 3), pattern="rte") == (1, 1)


def test_newest_job_is_the_default(tmp_path):
    sample(tmp_path)

    assert peek.newest_job(tmp_path) == 3


def test_screen_never_shows_names_paths_or_text(tmp_path):
    sample(tmp_path)

    screen = "\n".join(peek.summarize(tmp_path, pattern="rte"))

    assert "secret" not in screen.casefold()
    assert "Rte_" not in screen
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118


def test_missing_folder_or_job_says_what_to_do(tmp_path):
    assert "대화 기록이 없습니다" in "\n".join(peek.summarize(tmp_path / "nope"))
    sample(tmp_path)
    assert "작업 9의 대화 기록이 없습니다" in "\n".join(peek.summarize(tmp_path, job=9))


def test_default_job_comes_from_the_database_when_it_is_newer(tmp_path):
    # 작업 DB의 최근 작업에 대화 기록이 없으면 이전 작업으로 넘어가지 않고 그렇다고 말한다
    from llm_lab.store import NewRow, Store

    sample(tmp_path / "logs")
    store = Store(tmp_path / "fixer.db")
    for _ in range(4):
        store.create_job("secret.xlsx", "RTE_Result", 0, [], [NewRow(finding(), None, "no_source")])

    screen = "\n".join(peek.summarize(tmp_path / "logs", db=tmp_path / "fixer.db"))

    assert "작업 4의 대화 기록이 없습니다" in screen
    assert "작업 3:" not in screen


def test_jobs_that_reused_stored_answers_say_so(tmp_path):
    # 저장된 답을 다시 쓴 묶음은 기록에 보낸 내용이 없다. 없다고 하지 말고 재사용이라고 말한다
    text = render_log(
        job_id=9,
        kind="RTE",
        findings=[finding(row=2, file=r"C:\secret_dir\secret_file.c")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[],
        outcomes=["이전 결과 재사용 - LLM을 부르지 않음"],
    )
    write_log(tmp_path, 9, 2, text)
    write_log(tmp_path, 9, 3, text)

    screen = "\n".join(peek.summarize(tmp_path, job=9))

    assert "작업 9: 묶음 2개 모두 저장된 답을 다시 썼습니다" in screen
    assert "secret" not in screen.casefold()
    assert peek.reused_count(tmp_path, 9) == 2


def test_counts_which_names_manual_reasons_point_at(tmp_path):
    # 직접 수정 이유가 짚은 이름이 저장만 됐는지, 프로젝트에 없는지, 다 보냈는지 센다
    from llm_lab.csource import Definition
    from llm_lab.store import NewRow, Store

    def defined(name: str, file: str, first: int, last: int) -> Definition:
        return Definition(name, "function", file, first, last, "int x;")

    root = tmp_path / "secret_proj"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "secret_lib.h").write_text(
        "#define SECRET_WRAP(x) secret_missing(x)\nextern int secret_missing(int x);\n"
    )
    (root / "lib" / "secret_lib.c").write_text(
        "FUNC(int, CODE) secret_missing(int x)\n{\n    return secret_use(x);\n}\n"
        "void secret_use(int y)\n{\n    y = secret_missing(1);\n}\n"
    )
    store = Store(tmp_path / "fixer.db")
    store.create_job(
        "secret.xlsx",
        "RTE_Result",
        0,
        [],
        [NewRow(finding(), None, "no_source")],
        source_root=str(root),
        kind="rte",
        definitions=[
            defined("secret_api", "gen/Rte_secret.c", 1, 4),  # EDITABLE로 보냄
            defined("secret_deep", "gen/Rte_deep.c", 10, 20),  # 저장만 됨
            defined("secret_func", "secret_file.c", 1, 4),  # 보낸 코드 안
        ],
    )
    calls = CODE.replace("(void)", "(void) { return secret_missing() + secret_gone(); }")
    local = CODE.replace("(void)", "(void) { int secret_local = 1; }")
    logs = tmp_path / "logs"
    log(
        logs, 1, 2, HEAD + CODE + EDITABLE, answer("manual", reason="secret_deep의 구현을 고칩니다")
    )
    log(logs, 1, 3, HEAD + calls, answer("manual", reason="secret_missing가 정해야 합니다"))
    log(logs, 1, 4, HEAD + CODE + EDITABLE, answer("manual", reason="secret_api를 봐야 합니다"))
    log(logs, 1, 5, HEAD + CODE, answer("manual", reason="secret_func 안의 값이 문제"))
    log(logs, 1, 6, HEAD + CODE, answer("manual", reason="값을 정해야 합니다"))
    log(logs, 1, 7, HEAD + local, answer("manual", reason="secret_local이 0일 수 있습니다"))
    log(logs, 1, 8, HEAD + calls, answer("manual", reason="secret_gone 구현이 없습니다"))
    log(logs, 1, 9, HEAD + CODE, answer("fix"))

    found = dict(peek.reason_counts(peek.read_logs(logs, 1), store.definitions(1)))

    assert found == {"cut": 1, "missing_call": 2, "missing_other": 1, "sent": 2, "none": 1}
    out = tmp_path / "out"
    screen = "\n".join(peek.summarize(logs, db=tmp_path / "fixer.db", detail=out))
    assert "저장됐지만 요청에 안 붙은 정의" in screen
    assert "정의가 상한(40개)까지 붙은 묶음: 0 / 8, 직접 수정이 나온 묶음: 7" in screen
    assert (
        "정의를 못 찾은 호출 이름 2개: 다른 파일에 글자가 있음 1개, 프로젝트 어디에도 없음 1개"
        in screen
    )
    # 이름마다 자리의 모양을 센다: 매크로·선언·FUNC로 감싼 정의·호출
    assert "  호출 이름 1: 직접 수정 1행, 다른 파일 4곳, 검사 파일 0곳" in screen
    shapes = "매크로 1, 이어 붙임(##) 0, 선언 1, 정의 모양 1, 호출 1, 주석 0, 그 밖 0"
    assert f"    모양: {shapes}" in screen
    assert "  호출 이름 2: 직접 수정 1행, 다른 파일 0곳, 검사 파일 0곳" in screen
    assert "%TEMP%\\peek_logs_job1.txt" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118
    # 자세한 목록은 회사 PC의 임시 폴더에만 쓴다: 이유 원문과 이름이 찾은 자리
    detail = (out / "peek_logs_job1.txt").read_text(encoding="utf-8-sig")
    assert "secret_gone 구현이 없습니다" in detail
    assert "[매크로] lib/secret_lib.h:1:" in detail
    assert "== 짚은 이름이 모두 요청에 있었음 (2개)" in detail


def test_shape_tells_how_a_name_appears():
    assert peek.shape("#define SECRET_A(x) secret_f(x)", "secret_f") == "macro"
    assert peek.shape("#define SECRET_B(x) secret_##x()", "secret_f") == "pasted"
    assert peek.shape("extern int secret_f(int a);", "secret_f") == "declaration"
    assert peek.shape("static int *secret_f(void)", "secret_f") == "definition"
    assert peek.shape("FUNC(void, SECRET_CODE) secret_f(void)", "secret_f") == "definition"
    assert peek.shape("    return secret_f(1);", "secret_f") == "call"
    assert peek.shape("    x = secret_f(1);", "secret_f") == "call"
    assert peek.shape("    secret_f(1);", "secret_f") == "call"
    assert peek.shape("    table[0] = secret_f;", "secret_f") == "other"
    assert peek.shape("/* secret_f() */", "secret_f") == "comment"
