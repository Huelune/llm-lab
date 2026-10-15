import subprocess
import sys
import threading

import httpx2
from fake_llm import API_KEY, MODEL, error, make_client, scripted
from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer

from llm_lab.client import get_client
from llm_lab.config import Settings
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import Worker, daemon_pool

SOURCE = decode_source("calc.c", CALC_C)


def run_now(task):
    task()


def setup(tmp_path, handler, *rows: NewRow):
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now, log_dir=tmp_path / "logs")
    rows = rows or (NewRow(finding(), "calc.c", "pending"),)
    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], list(rows))
    return store, worker, job_id


def test_processes_pending_rows_and_finishes_job(tmp_path):
    handler, _ = scripted(fix_answer(), fix_answer("no_fix"))
    rows = (
        NewRow(finding(), "calc.c", "pending"),
        NewRow(finding(line=2, check="Overflow"), "calc.c", "pending"),
        NewRow(finding(), None, "no_source", "업로드한 소스에 calc.c이(가) 없음"),
    )
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)

    states = [(r.state, r.result.decision if r.result else None) for r in store.rows(job_id)]
    assert states == [("done", "fix"), ("done", "no_fix"), ("no_source", None)]
    assert store.job(job_id).state == "done"


def test_same_file_and_finding_reuse_cached_result(tmp_path):
    handler, sent = scripted(fix_answer())
    store, worker, first = setup(tmp_path, handler)
    worker.start(first)

    second = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [SOURCE], [NewRow(finding(), "calc.c", "pending")]
    )
    worker.start(second)

    assert len(sent) == 1
    assert store.rows(second)[0].result == store.rows(first)[0].result


def test_row_error_does_not_stop_the_job(tmp_path):
    handler, _ = scripted(error(400, "bad request"))
    store, worker, job_id = setup(tmp_path, handler)

    worker.start(job_id)

    row = store.rows(job_id)[0]
    assert row.state == "error"
    assert "요청 거부" in row.error
    assert store.job(job_id).state == "done"


def test_fatal_error_stops_job_and_retry_continues(tmp_path):
    rows = (NewRow(finding(), "calc.c", "pending"), NewRow(finding(line=2), "calc.c", "pending"))
    handler, sent = scripted(error(401, "invalid key"), fix_answer(), fix_answer())
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)

    job = store.job(job_id)
    assert job.state == "stopped"
    assert "인증" in job.message
    assert [r.state for r in store.rows(job_id)] == ["pending", "pending"]
    assert len(sent) == 1  # 두 번째 행은 보내지 않았다

    store.retry(job_id)
    worker.start(job_id)

    assert [r.state for r in store.rows(job_id)] == ["done", "done"]
    assert store.job(job_id).state == "done"


def test_resume_processes_rows_left_running(tmp_path):
    handler, _ = scripted(fix_answer())
    store, worker, job_id = setup(tmp_path, handler)
    store.set_row(store.rows(job_id)[0].id, "running")  # 처리 중에 서비스가 꺼졌다

    worker.resume()

    assert store.rows(job_id)[0].state == "done"
    assert store.job(job_id).state == "done"


def test_unexpected_failure_marks_row_error(tmp_path):
    handler, _ = scripted()
    store, worker, job_id = setup(tmp_path, handler, NewRow(finding(), "ghost.c", "pending"))

    worker.start(job_id)

    row = store.rows(job_id)[0]
    assert row.state == "error"
    assert row.error.startswith("처리 중 예상하지 못한 문제가 생겼습니다.\n할 일: ")
    assert store.job(job_id).state == "done"


def logs(tmp_path, job_id: str = "") -> list[str]:
    """대화 기록 파일의 글. job_id를 주면 그 작업의 것만."""
    pattern = f"*_job{job_id}_*.md" if job_id else "*.md"
    return [path.read_text(encoding="utf-8") for path in sorted((tmp_path / "logs").glob(pattern))]


def test_writes_conversation_log_for_each_row(tmp_path):
    handler, _ = scripted(fix_answer())
    store, worker, job_id = setup(tmp_path, handler)

    worker.start(job_id)

    (text,) = logs(tmp_path)
    assert f"# 작업 {job_id} · RTE · 엑셀 2행" in text
    assert "- 보낸 주소: https://llm.test/v1/chat/completions" in text
    assert "You review the Polyspace Code Prover run-time checks" in text  # 시스템 메시지 그대로
    assert "    3|     return a / b;" in text  # 보낸 코드 그대로
    assert "(b != 0) ? a / b : 0;" in text  # 받은 응답 그대로
    assert "- 결과: 수정안 - b가 0일 때를 검사합니다." in text
    assert API_KEY not in text


def test_log_for_reused_result_says_llm_was_not_called(tmp_path):
    handler, sent = scripted(fix_answer())
    store, worker, first = setup(tmp_path, handler)
    worker.start(first)
    second = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [SOURCE], [NewRow(finding(), "calc.c", "pending")]
    )

    worker.start(second)

    assert len(sent) == 1
    (text,) = logs(tmp_path, second)
    assert "LLM에 보낸 요청이 없습니다." in text
    assert "- 결과: 이전 결과 재사용 - LLM을 부르지 않음" in text


def test_log_records_row_error_and_fatal_stop(tmp_path):
    handler, _ = scripted(error(400, "bad request"), error(401, "invalid key"))
    second_row = finding(row=3, line=2)
    rows = (NewRow(finding(), "calc.c", "pending"), NewRow(second_row, "calc.c", "pending"))
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)  # 엑셀 2행은 400 오류, 3행은 401로 작업이 멈춘다

    error_log, stop_log = logs(tmp_path)
    assert "- 결과: 오류: LLM 서버가 요청을 처리하지 못했습니다. / 할 일: " in error_log
    assert "[기능 미지원]" in error_log
    assert "### 오류" in error_log
    assert "- 결과: 작업 중단: [인증]" in stop_log


def test_group_goes_out_in_one_request_and_each_row_gets_its_decision(tmp_path):
    # 묶음 안의 순서는 (줄, check, detail, 엑셀 행)이므로 Division by zero(엑셀 5행)가 1번이다
    handler, sent = scripted(
        answer(("fix", REASON), ("no_fix", "범위가 보장됨"), edits=[DIVIDE_FIX])
    )
    rows = (
        NewRow(finding(row=2, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
        NewRow(finding(row=5), "calc.c", "pending", grp="calc.c:3"),
    )
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)

    assert len(sent) == 1
    prompt = sent[0]["messages"][1]["content"]
    assert "Finding 1:\n- TYPE: Orange Check\n- check: Division by zero" in prompt
    overflow, division = store.rows(job_id)
    assert (overflow.result.decision, overflow.result.reason) == ("no_fix", "범위가 보장됨")
    assert (division.result.decision, division.result.reason) == ("fix", REASON)
    assert store.job(job_id).state == "done"
    (path,) = (tmp_path / "logs").glob("*.md")
    assert path.name.endswith(f"_job{job_id}_row2.md")  # 묶음의 가장 작은 엑셀 행
    text = path.read_text(encoding="utf-8")
    assert f"# 작업 {job_id} · RTE · 엑셀 5·2행" in text
    assert "- 결과: 엑셀 5행 수정안 - b가 0일 때를 검사합니다.\n" in text
    assert "- 결과: 엑셀 2행 수정 불필요 - 범위가 보장됨\n" in text


def test_group_result_is_reused_when_only_excel_rows_change(tmp_path):
    handler, sent = scripted(
        answer(("fix", REASON), ("no_fix", "범위가 보장됨"), edits=[DIVIDE_FIX])
    )
    first_rows = (
        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
        NewRow(finding(row=5, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
    )
    store, worker, first = setup(tmp_path, handler, *first_rows)
    worker.start(first)
    # 엑셀을 다시 뽑아 행 번호와 행 순서만 바뀌었다
    rows = [
        NewRow(finding(row=12, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
        NewRow(finding(row=8), "calc.c", "pending", grp="calc.c:3"),
    ]
    second = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)

    worker.start(second)

    assert len(sent) == 1
    overflow, division = store.rows(second)
    assert (overflow.result.decision, division.result.decision) == ("no_fix", "fix")


def test_same_rule_twice_on_one_line_gets_an_answer_each(tmp_path):
    handler, sent = scripted(answer(("fix", REASON), ("fix", REASON), edits=[DIVIDE_FIX]))
    rows = (
        NewRow(finding(row=4), "calc.c", "pending", grp="calc.c:3"),
        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
    )
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)

    assert len(sent) == 1
    assert "Finding 2:" in sent[0]["messages"][1]["content"]
    assert [row.result.decision for row in store.rows(job_id)] == ["fix", "fix"]


def test_groups_are_queued_most_important_first(tmp_path):
    queued = []
    handler, sent = scripted(fix_answer(), fix_answer())
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, queued.append)
    red = finding(row=3, color="Red", type="Red Check", check="Overflow")
    rows = [NewRow(finding(row=2), "calc.c", "pending"), NewRow(red, "calc.c", "pending")]
    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)

    worker.start(job_id)
    for task in queued:
        task()

    # 엑셀에서는 Orange가 먼저지만 Red를 먼저 보낸다
    assert ["- TYPE: Red Check" in s["messages"][1]["content"] for s in sent] == [True, False]


def test_stopped_job_leaves_queued_groups_until_it_continues(tmp_path):
    queued = []
    handler, sent = scripted(fix_answer(), fix_answer())
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, queued.append)
    rows = [NewRow(finding(), "calc.c", "pending"), NewRow(finding(line=2), "calc.c", "pending")]
    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)
    worker.start(job_id)

    store.stop(job_id)
    for task in queued:
        task()

    assert sent == []
    assert [r.state for r in store.rows(job_id)] == ["pending", "pending"]
    assert store.job(job_id).state == "stopped"

    queued.clear()
    store.retry(job_id)  # "이어서 처리"
    worker.start(job_id)
    for task in queued:
        task()

    assert [r.state for r in store.rows(job_id)] == ["done", "done"]
    assert store.job(job_id).state == "done"


def test_group_error_marks_every_row_and_fatal_error_puts_them_back(tmp_path):
    rows = (
        NewRow(finding(row=2), "calc.c", "pending", grp="calc.c:3"),
        NewRow(finding(row=3, check="Overflow"), "calc.c", "pending", grp="calc.c:3"),
    )
    handler, _ = scripted(error(400, "bad request"))
    store, worker, job_id = setup(tmp_path / "a", handler, *rows)

    worker.start(job_id)

    states = [(r.state, r.error) for r in store.rows(job_id)]
    assert [state for state, _ in states] == ["error", "error"]
    assert states[0][1] == states[1][1] != ""

    handler, _ = scripted(error(401, "invalid key"))
    store, worker, job_id = setup(tmp_path / "b", handler, *rows)

    worker.start(job_id)

    assert [r.state for r in store.rows(job_id)] == ["pending", "pending"]
    assert store.job(job_id).state == "stopped"


def test_daemon_pool_runs_tasks():
    done = threading.Event()

    daemon_pool(2)(done.set)

    assert done.wait(5)


def test_daemon_pool_does_not_hold_up_exit():
    # LLM 응답을 기다리는 작업이 있어도 Ctrl+C 뒤 프로세스가 바로 끝나야 한다
    code = (
        "import threading\n"
        "from llm_lab.worker import daemon_pool\n"
        "daemon_pool(1)(threading.Event().wait)\n"
    )

    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)


def test_log_shows_the_azure_deployment_path(tmp_path):
    # Azure 방식은 SDK가 경로를 바꾸므로 기록의 주소도 실제 경로여야 한다
    handler, _ = scripted(fix_answer())
    settings = Settings(
        base_url="https://llm.test", api_key=API_KEY, model=MODEL, client_kind="azure"
    )
    client = get_client(settings).with_options(
        max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, client, MODEL, run_now, log_dir=tmp_path / "logs")
    rows = [NewRow(finding(), "calc.c", "pending")]
    job_id = store.create_job("rte.xlsx", "RTE_Result", 0, [SOURCE], rows)

    worker.start(job_id)

    (text,) = logs(tmp_path)
    assert "- 보낸 주소: https://llm.test/openai/deployments/m1/chat/completions" in text
