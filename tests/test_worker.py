from fake_llm import MODEL, error, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import Worker

SOURCE = decode_source("calc.c", CALC_C)


def run_now(task):
    task()


def setup(tmp_path, handler, *rows: NewRow):
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now)
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
