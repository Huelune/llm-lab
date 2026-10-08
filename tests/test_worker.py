from fake_llm import API_KEY, MODEL, error, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import Worker

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
    assert "서버 로그" in row.error
    assert store.job(job_id).state == "done"


def logs(tmp_path) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted((tmp_path / "logs").glob("*.md"))]


def test_writes_conversation_log_for_each_row(tmp_path):
    handler, _ = scripted(fix_answer())
    store, worker, job_id = setup(tmp_path, handler)

    worker.start(job_id)

    (text,) = logs(tmp_path)
    assert f"# 작업 {job_id} · 엑셀 2행 · Orange Check" in text
    assert "- 보낸 주소: https://llm.test/v1/chat/completions" in text
    assert "You review one Polyspace Code Prover" in text  # 시스템 메시지 그대로
    assert "    3|     return a / b;" in text  # 보낸 코드 그대로
    assert "(b != 0) ? a / b : 0;" in text  # 받은 응답 그대로
    assert "- 결과: 수정 - b가 0일 때를 검사합니다." in text
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
    assert "LLM에 보낸 요청이 없습니다." in logs(tmp_path)[-1]
    assert "- 결과: 이전 결과 재사용 - LLM을 부르지 않음" in logs(tmp_path)[-1]


def test_log_records_row_error_and_fatal_stop(tmp_path):
    handler, _ = scripted(error(400, "bad request"), error(401, "invalid key"))
    second_row = finding(row=3, line=2)
    rows = (NewRow(finding(), "calc.c", "pending"), NewRow(second_row, "calc.c", "pending"))
    store, worker, job_id = setup(tmp_path, handler, *rows)

    worker.start(job_id)  # 엑셀 2행은 400 오류, 3행은 401로 작업이 멈춘다

    error_log, stop_log = logs(tmp_path)
    assert "- 결과: 오류: [기능 미지원]" in error_log
    assert "### 오류" in error_log
    assert "- 결과: 작업 중단: [인증]" in stop_log
