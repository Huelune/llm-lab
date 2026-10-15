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


def uid(n: int) -> str:
    """앞 8자가 서로 다른 시험용 작업 ID."""
    return f"{n:08x}-0000-4000-8000-000000000000"


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


def width(line: str) -> int:
    """터미널에서 차지하는 칸 수 (한글은 2칸)."""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line)


def log(folder: Path, job: str, row: int, user: str, content: str) -> None:
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
    log(folder, uid(3), 2, HEAD + CODE + EDITABLE, answer("manual"))
    log(folder, uid(3), 3, HEAD + CODE + EDITABLE + CLIPPED, answer("manual"))
    log(folder, uid(3), 4, HEAD + CODE + CALLER, answer("manual"))
    log(folder, uid(3), 5, HEAD + CODE + REFERENCE, answer("manual"))
    log(folder, uid(3), 6, HEAD + CODE, answer("manual"))
    other = {"file": "gen/Rte_secret.c", "original": "a", "replacement": "b"}
    log(folder, uid(3), 7, HEAD + CODE + EDITABLE, answer("fix", [other]))
    log(folder, uid(3), 8, HEAD + CODE, answer("no_fix"))
    log(folder, uid(2), 2, HEAD + CODE, answer("manual"))  # 예전 작업은 세지 않는다


def test_counts_manual_rows_by_what_was_sent(tmp_path):
    sample(tmp_path)

    screen = "\n".join(peek.summarize(tmp_path, pattern="rte"))

    assert "작업 00000003: 묶음 7개" in screen
    assert "수정안 1 / 수정 불필요 1 / 직접 수정 5" in screen
    assert peek.other_file_fixes(peek.read_logs(tmp_path, uid(3)), pattern="rte") == (1, 1)


def test_newest_job_is_the_default(tmp_path):
    sample(tmp_path)

    assert peek.newest_job(tmp_path) == uid(3)


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
    assert peek.NOT_FOUND in "\n".join(peek.summarize(tmp_path, job=uid(9)[:8]))


def test_default_job_comes_from_the_database_when_it_is_newer(tmp_path):
    # 작업 DB의 최근 작업에 대화 기록이 없으면 이전 작업으로 넘어가지 않고 그렇다고 말한다
    from llm_lab.store import NewRow, Store

    sample(tmp_path / "logs")
    store = Store(tmp_path / "fixer.db")
    for _ in range(4):
        job = store.create_job(
            "secret.xlsx", "RTE_Result", 0, [], [NewRow(finding(), None, "no_source")]
        )

    screen = "\n".join(peek.summarize(tmp_path / "logs", db=tmp_path / "fixer.db"))

    assert f"작업 {job[:8]}의 대화 기록이 없습니다" in screen
    assert "작업 00000003" not in screen


def test_jobs_that_reused_stored_answers_say_so(tmp_path):
    # 저장된 답을 다시 쓴 묶음은 기록에 보낸 내용이 없다. 없다고 하지 말고 재사용이라고 말한다
    text = render_log(
        job_id=uid(9),
        kind="RTE",
        findings=[finding(row=2, file=r"C:\secret_dir\secret_file.c")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[],
        outcomes=["이전 결과 재사용 - LLM을 부르지 않음"],
    )
    write_log(tmp_path, uid(9), 2, text)
    write_log(tmp_path, uid(9), 3, text)

    screen = "\n".join(peek.summarize(tmp_path, job=uid(9)))

    assert "작업 00000009: 묶음 2개 모두 저장된 답을 다시 썼습니다" in screen
    assert "secret" not in screen.casefold()
    assert peek.reused_count(tmp_path, uid(9)) == 2


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
    job = store.create_job(
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
        logs,
        job,
        2,
        HEAD + CODE + EDITABLE,
        answer("manual", reason="secret_deep의 구현을 고칩니다"),
    )
    log(logs, job, 3, HEAD + calls, answer("manual", reason="secret_missing가 정해야 합니다"))
    log(logs, job, 4, HEAD + CODE + EDITABLE, answer("manual", reason="secret_api를 봐야 합니다"))
    log(logs, job, 5, HEAD + CODE, answer("manual", reason="secret_func 안의 값이 문제"))
    log(logs, job, 6, HEAD + CODE, answer("manual", reason="값을 정해야 합니다"))
    log(logs, job, 7, HEAD + local, answer("manual", reason="secret_local이 0일 수 있습니다"))
    log(logs, job, 8, HEAD + calls, answer("manual", reason="secret_gone 구현이 없습니다"))
    log(logs, job, 9, HEAD + CODE, answer("fix"))

    found = dict(peek.reason_counts(peek.read_logs(logs, job), store.definitions(job)))

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
    assert f"%TEMP%\\peek_logs_job{job[:8]}.txt" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118
    # 자세한 목록은 회사 PC의 임시 폴더에만 쓴다: 이유 원문과 이름이 찾은 자리
    detail = (out / f"peek_logs_job{job[:8]}.txt").read_text(encoding="utf-8-sig")
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


def test_counts_check_kinds_of_manual_rows_with_standard_names_only(tmp_path):
    # 검사 이름은 Polyspace 표준 이름일 때만 찍는다. 엑셀 칸에 다른 글이 있으면 "그 밖"으로 센다
    def head(color: str, check: str) -> str:
        return (
            "Polyspace Code Prover results in one C function:\n\nFinding 1:\n"
            f"- TYPE: {color} Check\n- check: {check}\n- File: secret_file.c\n"
        )

    log(tmp_path, uid(3), 2, head("Red", "Non-terminating call") + CODE, answer("manual"))
    log(tmp_path, uid(3), 3, head("Red", "Non-terminating call") + CODE, answer("manual"))
    log(tmp_path, uid(3), 4, head("Orange", "overflow") + CODE, answer("manual"))
    log(tmp_path, uid(3), 5, head("Orange", "secret custom check") + CODE, answer("manual"))
    log(tmp_path, uid(3), 6, head("Orange", "Overflow") + CODE, answer("fix"))

    assert peek.manual_checks(peek.read_logs(tmp_path, uid(3))) == [
        ("Red Non-terminating call", 2),
        ("Orange Overflow", 1),
        ("그 밖", 1),
    ]
    screen = "\n".join(peek.summarize(tmp_path, job=uid(3)))
    assert "직접 수정 행의 검사 종류:" in screen
    assert "  Red Non-terminating call" in screen
    assert "secret" not in screen.casefold()


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
        job_id=uid(4),
        kind="RTE",
        findings=[finding(row=2, file=r"C:\secret_dir\secret_file.c")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[exchange],
        outcomes=["정의 되묻기 1번", "secret outcome"],
    )
    write_log(tmp_path, uid(4), 2, text)
    log(tmp_path, uid(4), 3, HEAD + CODE, answer("no_fix"))

    counts = peek.follow_up_counts(tmp_path, uid(4))

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
    screen = "\n".join(peek.summarize(tmp_path, job=uid(4)))
    assert "되묻기: 되물은 묶음 1 / 2 (1번 1, 2번 0, 3번 0)" in screen
    assert "요청한 이름 3: 보냄 1, 프로젝트에 없음 1, 이미 보냄 1" in screen
    assert "  상한으로 못 보냄 0, 찾을 수 없었음 0" in screen
    assert max(width(line) for line in screen.splitlines()) <= 118
    assert "되묻기 뒤 판단이 바뀐 행: 1" in screen
    assert "판단 행: 수정안 1 / 수정 불필요 1 / 직접 수정 0" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118


def talk(folder: Path, job: str, row: int, messages: list[str], content: str | None) -> None:
    """되묻기 대화 기록 하나. messages는 user, assistant가 번갈아 오고, content가 None이면 오류."""
    roles = ("user", "assistant")
    exchange = Exchange(
        [
            {"role": "system", "content": "secret system"},
            *({"role": roles[i % 2], "content": m} for i, m in enumerate(messages)),
        ],
        raw=None if content is None else {"secret": "raw"},
        content=content or "",
        finish_reason=None if content is None else "stop",
        error="secret error" if content is None else "",
    )
    text = render_log(
        job_id=job,
        kind="RTE",
        findings=[finding(row=row, file=r"C:\secret_dir\secret_file.c")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[exchange],
        outcomes=["정의 되묻기 1번", "secret outcome"],
    )
    write_log(folder, job, row, text)


def provisional(*need: str) -> str:
    body = {"findings": [{"number": 1, "decision": "manual", "reason": "secret why"}]}
    return json.dumps({**body, "edits": [], "need": list(need)})


def test_names_received_in_a_follow_up_count_as_sent(tmp_path):
    # 첫 요청에 없었어도 되물어 받은 이름은 "요청에 있었음"이다
    # 프로젝트에 없다고 들은 이름은 아니다
    from llm_lab.followups import ASKED_EDITABLE, LAST_LINE, MISSING_LINE
    from llm_lab.store import NewRow, Store

    store = Store(tmp_path / "fixer.db")
    job = store.create_job(
        "secret.xlsx", "RTE_Result", 0, [], [NewRow(finding(), None, "no_source")], kind="rte"
    )
    calls = CODE.replace("(void)", "(void) { return secret_got() + secret_none(); }")
    follow_up = (
        ASKED_EDITABLE
        + "\n\n```c\n// editable: gen/secret.c lines 1-2\n    1| int secret_got(void)\n```\n\n"
        + MISSING_LINE
        + "secret_none.\n\n"
        + LAST_LINE
    )
    logs = tmp_path / "logs"
    messages = [HEAD + calls, provisional("secret_got()", "secret_none"), follow_up]
    talk(logs, job, 2, messages, answer("manual", reason="secret_got가 정해야 합니다"))
    talk(logs, job, 3, messages, answer("manual", reason="secret_none 구현이 없습니다"))

    screen = "\n".join(peek.summarize(logs, db=tmp_path / "fixer.db"))

    assert f"  {peek.REASON_LABELS['sent']:<28}{1:>6}" in screen
    assert f"  {peek.REASON_LABELS['missing_call']:<28}{1:>6}" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118
    found = dict(peek.reason_counts(peek.read_logs(logs, job), [], peek.received_names(logs, job)))
    assert found == {"cut": 0, "missing_call": 1, "missing_other": 0, "sent": 1, "none": 0}


def test_follow_ups_count_only_logs_with_an_answer(tmp_path):
    # 답을 못 받은 기록(오류)은 묶음 수에서 빠지므로 되물은 묶음에서도 뺀다
    from llm_lab.followups import LAST_LINE, MISSING_LINE

    follow_up = MISSING_LINE + "secret_x.\n\n" + LAST_LINE
    messages = [HEAD + CODE, provisional("secret_x"), follow_up]
    talk(tmp_path, uid(5), 2, messages, answer("manual"))
    talk(tmp_path, uid(5), 3, messages, None)

    assert peek.follow_up_counts(tmp_path, uid(5))["groups"] == 1
    screen = "\n".join(peek.summarize(tmp_path, job=uid(5)))
    assert "되묻기: 되물은 묶음 1 / 1 " in screen
    assert "secret" not in screen.casefold()


def test_edits_that_name_the_checked_file_are_not_other_file_fixes(tmp_path):
    # LLM이 검사 파일 수정에 file을 ""가 아닌 그 파일 경로로 적어도 서버는 검사 파일 수정으로 받는다
    own = [{"file": ".\\SECRET_FILE.c", "original": "a", "replacement": "b"}]
    log(tmp_path, uid(6), 2, HEAD + CODE, answer("fix", own))

    assert peek.other_file_fixes(peek.read_logs(tmp_path, uid(6))) == (0, 0)
    assert peek.other_file_places(tmp_path, uid(6))["other"] == 0
    assert "다른 파일을 고친 수정 행: 0" in "\n".join(peek.summarize(tmp_path, job=uid(6)))


def test_other_file_fixes_are_split_by_where_the_edited_file_was_sent(tmp_path):
    # 다른 파일을 고친 수정이 호출자·첫 요청의 정의·되물어 받은 정의·헤더 중 어디인지 센다
    from llm_lab.followups import LAST_LINE

    def fix(file: str) -> str:
        return answer("fix", [{"file": file, "original": "a", "replacement": "b"}])

    log(tmp_path, uid(5), 2, HEAD + CODE + CALLER, fix("app/secret_main.c"))
    log(tmp_path, uid(5), 3, HEAD + CODE + EDITABLE, fix("gen/Rte_secret.c"))
    asked = "Definitions you asked for.\n\n```c\n// editable: inc/secret_deep.h lines 1-2\n```"
    exchange = Exchange(
        [
            {"role": "system", "content": "secret system"},
            {"role": "user", "content": HEAD + CODE},
            {"role": "assistant", "content": json.dumps({"need": ["secret_deep"]})},
            {"role": "user", "content": asked + "\n\n" + LAST_LINE},
        ],
        raw={},
        content=fix("inc/secret_deep.h"),
        finish_reason="stop",
    )
    text = render_log(
        job_id=uid(5),
        kind="RTE",
        findings=[finding(row=4)],
        source_name="secret_file.c",
        model="m",
        endpoint="e",
        extra_body={},
        exchanges=[exchange],
        outcomes=["x"],
    )
    write_log(tmp_path, uid(5), 4, text)

    counts = peek.other_file_places(tmp_path, uid(5))

    assert counts == {"caller": 1, "definition": 1, "asked": 1, "header": 1, "other": 0}
    screen = "\n".join(peek.summarize(tmp_path, job=uid(5)))
    assert (
        "  고친 곳: 호출자 1, 첫 요청의 정의 1, 되물어 받은 정의 1, 그 밖 0 / 헤더(.h) 1" in screen
    )
    assert "secret" not in screen.casefold()


def test_job_is_found_by_an_id_prefix(tmp_path):
    sample(tmp_path)

    short = "\n".join(peek.summarize(tmp_path, job=uid(3)[:8]))
    longer = "\n".join(peek.summarize(tmp_path, job=uid(3)[:12].upper()))

    assert "작업 00000003: 묶음 7개" in short
    assert "작업 00000003: 묶음 7개" in longer
    assert "secret" not in short.casefold()


def test_prefix_that_matches_several_or_none_says_what_to_do(tmp_path):
    log(tmp_path, "abcd0001-0000-4000-8000-000000000000", 2, HEAD + CODE, answer("manual"))
    log(tmp_path, "abcd0002-0000-4000-8000-000000000000", 2, HEAD + CODE, answer("manual"))

    assert peek.summarize(tmp_path, job="abcd") == [peek.AMBIGUOUS]
    assert peek.summarize(tmp_path, job="abcd0001")[0].startswith("작업 abcd0001:")
    assert peek.summarize(tmp_path, job="zzzz") == [peek.NOT_FOUND]
    assert peek.summarize(tmp_path, job="abc") == [peek.NOT_FOUND]  # 4자 미만


def test_prefix_is_matched_against_the_database_when_there_is_one(tmp_path):
    from llm_lab.store import NewRow, Store

    store = Store(tmp_path / "fixer.db")
    job = store.create_job("secret.xlsx", "RTE_Result", 0, [], [NewRow(finding(), None, "x")])

    screen = "\n".join(peek.summarize(tmp_path / "logs", job=job[:6], db=tmp_path / "fixer.db"))

    assert f"작업 {job[:8]}의 대화 기록이 없습니다" in screen
    assert peek.newest_db_job(tmp_path / "fixer.db") == job


OLD_DB_MESSAGE = (
    "예전 작업 기록 형식입니다. 서버를 끈 뒤 uv run llm-fix-server --clear all 을 실행하세요."
)


def old_format_db(tmp_path):
    import sqlite3
    from contextlib import closing

    (tmp_path / "private").mkdir()
    with closing(sqlite3.connect(tmp_path / "private" / "fixer.db")) as db:
        db.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, created_at TEXT)")
        db.execute("INSERT INTO jobs VALUES (7, '2026-01-01T00:00:00')")
        db.execute("PRAGMA user_version = 5")
        db.commit()


def test_old_format_database_says_what_to_do(tmp_path, monkeypatch, capsys):

    old_format_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(peek, "clear_screen", lambda: None)
    for argv in ([], ["--job", "0007"]):
        monkeypatch.setattr(sys, "argv", ["peek", *argv])
        assert peek.main() == 1
        out = capsys.readouterr().out
        assert out.strip() == OLD_DB_MESSAGE
