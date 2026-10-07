from llm_lab.fixer import Edit, FixResult
from llm_lab.polyspace import Finding
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store

SOURCE = decode_source("calc.c", "/* 합계 */\r\nint x;\r\n".encode("cp949"))
FINDING = Finding(
    row=2,
    color="Red",
    type="Red Check",
    file=r"C:\proj\calc.c",
    line=2,
    check="Overflow",
    detail="overflow",
)
RESULT = FixResult("fix", "이유", (Edit(0, 3, "y"),), "diff")


def make_job(store: Store, *states: str) -> int:
    rows = [NewRow(FINDING, "calc.c" if s != "no_source" else None, s) for s in states]
    return store.create_job("rte.xlsx", "RTE_Result", 3, [SOURCE], rows)


def test_job_rows_and_sources_round_trip(tmp_path):
    store = Store(tmp_path / "sub" / "fixer.db")

    job_id = make_job(store, "pending", "no_source")

    job = store.job(job_id)
    assert (job.excel_name, job.sheet, job.skipped, job.state) == (
        "rte.xlsx",
        "RTE_Result",
        3,
        "running",
    )
    rows = store.rows(job_id)
    assert [(r.finding, r.file_name, r.state) for r in rows] == [
        (FINDING, "calc.c", "pending"),
        (FINDING, None, "no_source"),
    ]
    assert store.source(job_id, "calc.c") == SOURCE
    assert store.job(999) is None


def test_job_without_pending_rows_is_done_at_once(tmp_path):
    store = Store(tmp_path / "fixer.db")

    assert store.job(make_job(store, "no_source")).state == "done"


def test_set_row_and_finish_when_nothing_left(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job_id = make_job(store, "pending", "pending")
    first, second = store.rows(job_id)

    store.set_row(first.id, "done", result=RESULT)
    store.finish_if_done(job_id)
    assert store.job(job_id).state == "running"

    store.set_row(second.id, "error", error="실패")
    store.finish_if_done(job_id)
    assert store.job(job_id).state == "done"
    assert store.row(first.id).result == RESULT
    assert store.row(second.id).error == "실패"


def test_retry_resets_error_rows_and_revives_job(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job_id = make_job(store, "pending", "no_source")
    row = store.rows(job_id)[0]
    store.set_row(row.id, "error", error="실패")
    store.set_job(job_id, "stopped", "네트워크 오류")

    store.retry(job_id)

    assert store.job(job_id).state == "running"
    assert store.job(job_id).message == ""
    assert [(r.state, r.error) for r in store.rows(job_id)] == [("pending", ""), ("no_source", "")]


def test_recover_returns_running_jobs_and_requeues_running_rows(tmp_path):
    store = Store(tmp_path / "fixer.db")
    running = make_job(store, "pending")
    done = make_job(store, "no_source")
    store.set_row(store.rows(running)[0].id, "running")

    assert store.recover() == [running]
    assert store.rows(running)[0].state == "pending"
    assert done not in store.recover()


def test_cache_round_trip(tmp_path):
    store = Store(tmp_path / "fixer.db")

    assert store.cache_get("k") is None
    store.cache_put("k", RESULT)
    assert store.cache_get("k") == RESULT


def test_jobs_lists_newest_first(tmp_path):
    store = Store(tmp_path / "fixer.db")
    ids = [make_job(store, "pending") for _ in range(3)]

    assert [job.id for job in store.jobs(limit=2)] == [ids[2], ids[1]]
