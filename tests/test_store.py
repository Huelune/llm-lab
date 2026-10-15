import sqlite3
from contextlib import closing

import pytest
from rte_fixtures import CALC_C, finding

from llm_lab.csource import Definition
from llm_lab.fixer import Edit, FixResult
from llm_lab.polyspace import Finding
from llm_lab.sources import decode_source
from llm_lab.store import SCHEMA_VERSION, USER_STOP, NewRow, Store, StoreError

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


def test_cache_keeps_the_results_of_a_group_in_order(tmp_path):
    store = Store(tmp_path / "fixer.db")
    results = [RESULT, FixResult("no_fix", "범위가 보장됨", (), "")]

    assert store.cache_get("k") is None
    store.cache_put("k", results)
    assert store.cache_get("k") == results


def test_cache_entry_from_before_groups_is_ignored(tmp_path):
    store = Store(tmp_path / "fixer.db")
    with closing(sqlite3.connect(tmp_path / "fixer.db")) as db:  # 결과 하나를 그대로 저장하던 때
        db.execute("INSERT INTO cache VALUES ('k', ?, '2026-10-07T00:00:00')", (RESULT.to_json(),))
        db.commit()

    assert store.cache_get("k") is None


def test_jobs_lists_newest_first(tmp_path):
    store = Store(tmp_path / "fixer.db")
    ids = [make_job(store, "pending") for _ in range(3)]

    assert [job.id for job in store.jobs(limit=2)] == [ids[2], ids[1]]


def test_claim_rows_takes_the_whole_group_or_nothing(tmp_path):
    store = Store(tmp_path / "fixer.db")
    first, second = (row.id for row in store.rows(make_job(store, "pending", "pending")))

    assert store.claim_rows([first, second]) is True
    assert store.claim_rows([first, second]) is False
    assert [store.row(i).state for i in (first, second)] == ["running", "running"]

    store.set_row(second, "pending")
    assert store.claim_rows([first, second]) is False  # 하나라도 pending이 아니면 그대로 둔다
    assert store.row(second).state == "pending"


def test_job_keeps_kind_note_and_context_and_rows_keep_their_group(tmp_path):
    store = Store(tmp_path / "fixer.db")
    rows = [
        NewRow(FINDING, "calc.c", "pending", grp="calc.c:2"),
        NewRow(FINDING, "calc.c", "pending"),
    ]

    job_id = store.create_job(
        "m.xlsx", "MISRA_Result", 0, [SOURCE], rows, kind="misra", note="메모", context='{"a": 1}'
    )

    job = store.job(job_id)
    assert (job.kind, job.note, job.context) == ("misra", "메모", '{"a": 1}')
    assert [row.grp for row in store.rows(job_id)] == ["calc.c:2", ""]
    old_style = store.job(make_job(store, "pending"))
    assert (old_style.kind, old_style.note, old_style.context) == ("rte", "", "")


def test_stop_only_stops_running_jobs(tmp_path):
    store = Store(tmp_path / "fixer.db")
    running = make_job(store, "pending")
    done = make_job(store, "no_source")

    store.stop(running)
    store.stop(done)

    assert (store.job(running).state, store.job(running).message) == ("stopped", USER_STOP)
    assert store.job(done).state == "done"


def test_user_stopped_job_with_nothing_left_becomes_done(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job_id = make_job(store, "pending", "no_source")
    store.stop(job_id)  # 마지막 묶음이 LLM 답을 기다리는 중에 멈췄다
    store.set_row(store.rows(job_id)[0].id, "done", result=RESULT)  # 그 묶음이 끝났다

    store.finish_if_done(job_id)

    job = store.job(job_id)
    assert (job.state, job.message) == ("done", "")


def test_user_stopped_job_with_a_pending_row_stays_stopped(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job_id = make_job(store, "pending", "pending")
    store.stop(job_id)
    store.set_row(store.rows(job_id)[0].id, "done", result=RESULT)

    store.finish_if_done(job_id)

    job = store.job(job_id)
    assert (job.state, job.message) == ("stopped", USER_STOP)


def test_job_stopped_by_another_reason_is_not_closed_even_with_nothing_left(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job_id = make_job(store, "pending")
    store.set_row(store.rows(job_id)[0].id, "done", result=RESULT)
    store.set_job(job_id, "stopped", "네트워크 오류")

    store.finish_if_done(job_id)

    job = store.job(job_id)
    assert (job.state, job.message) == ("stopped", "네트워크 오류")


def user_version(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def set_user_version(path, version):
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"PRAGMA user_version = {version}")


def test_new_database_records_schema_version(tmp_path):
    Store(tmp_path / "fixer.db")

    assert user_version(tmp_path / "fixer.db") == SCHEMA_VERSION


def test_unversioned_database_from_first_release_is_kept(tmp_path):
    path = tmp_path / "fixer.db"
    job_id = make_job(Store(path), "pending")
    set_user_version(path, 0)

    store = Store(path)

    assert user_version(path) == SCHEMA_VERSION
    assert store.job(job_id) is not None


def test_refuses_database_from_newer_version(tmp_path):
    path = tmp_path / "fixer.db"
    Store(path)
    set_user_version(path, SCHEMA_VERSION + 1)

    with pytest.raises(StoreError, match="새 버전"):
        Store(path)


def test_job_keeps_source_folder_and_recent_folders_come_newest_first(tmp_path):
    store = Store(tmp_path / "fixer.db")
    for root in ["C:/work/a", "C:/work/b", "C:/work/a"]:
        store.create_job("rte.xlsx", "RTE_Result", 0, [], [], source_root=root)
    store.create_job("rte.xlsx", "RTE_Result", 0, [], [])  # 폴더 없이 만든 예전 방식 작업

    assert store.job(1).source_root == "C:/work/a"
    assert store.job(4).source_root == ""
    assert store.recent_roots() == ["C:/work/a", "C:/work/b"]


def test_database_from_schema_1_gets_source_folder_column(tmp_path):
    path = tmp_path / "fixer.db"
    job_id = make_job(Store(path), "pending")
    with closing(sqlite3.connect(path)) as db:  # 첫 배포의 jobs 표에는 이 칸이 없었다
        db.execute("ALTER TABLE jobs DROP COLUMN source_root")
        db.execute("PRAGMA user_version = 1")
        db.commit()

    store = Store(path)

    assert user_version(path) == SCHEMA_VERSION == 5
    assert store.job(job_id).source_root == ""


def test_database_from_schema_2_gets_kind_note_context_and_group_columns(tmp_path):
    path = tmp_path / "fixer.db"
    job_id = make_job(Store(path), "pending")
    with closing(sqlite3.connect(path)) as db:  # 스키마 2에는 이 칸들이 없었다
        for table, column in [("jobs", "kind"), ("jobs", "note"), ("jobs", "context")]:
            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        db.execute("ALTER TABLE rows DROP COLUMN grp")
        db.execute("PRAGMA user_version = 2")
        db.commit()

    store = Store(path)

    assert user_version(path) == SCHEMA_VERSION
    job = store.job(job_id)
    assert (job.kind, job.note, job.context) == ("rte", "", "")
    assert store.rows(job_id)[0].grp == ""


DEFINITIONS = [
    Definition("BUF_LEN", "macro", "inc/limits.h", 3, 3, "#define BUF_LEN 16"),
    Definition("BUF_LEN", "macro", "inc/limits.h", 7, 7, "#define BUF_LEN 32"),
    Definition("lookup", "variable", "a.c", 3, 5, "static const u8 lookup[3] = {\n    1, 2, 3\n};"),
]


def test_schema_version_is_five():
    assert SCHEMA_VERSION == 5


def test_definitions_round_trip_in_name_file_line_order(tmp_path):
    store = Store(tmp_path / "fixer.db")
    rows = [NewRow(FINDING, "calc.c", "pending")]

    job_id = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [SOURCE], rows, definitions=reversed(DEFINITIONS)
    )

    assert store.definitions(job_id) == DEFINITIONS
    assert store.definitions(job_id + 1) == []


def test_job_without_definitions_has_an_empty_list(tmp_path):
    store = Store(tmp_path / "fixer.db")

    job_id = make_job(store, "pending")

    assert store.definitions(job_id) == []


def test_sources_returns_every_file_of_a_job(tmp_path):
    store = Store(tmp_path / "fixer.db")
    other = decode_source("lib/drv.c", b"int drv;\n")
    job_id = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [SOURCE, other], [NewRow(FINDING, "calc.c", "pending")]
    )

    assert set(store.sources(job_id)) == {"calc.c", "lib/drv.c"}
    assert store.sources(job_id)["lib/drv.c"] == other
    assert store.sources(job_id + 1) == {}


def test_add_files_keeps_what_the_job_already_has(tmp_path):
    store = Store(tmp_path / "fixer.db")
    source = decode_source("calc.c", CALC_C)
    job = store.create_job(
        "x.xlsx", "RTE_Result", 0, [source], [NewRow(finding(), "calc.c", "pending")]
    )
    changed = decode_source("calc.c", b"int other;\n")
    extra = decode_source("lib/deep.c", b"int deep(void);\n")

    store.add_files(job, [changed, extra])

    assert store.sources(job)["calc.c"].text == source.text
    assert store.sources(job)["lib/deep.c"].text == extra.text


def test_cache_keeps_the_files_an_answer_relied_on(tmp_path):
    store = Store(tmp_path / "fixer.db")
    result = FixResult("no_fix", "이유", (), "")

    store.cache_put("plain", [result])
    store.cache_put("asked", [result], files=[("lib/deep.c", "abc")])

    assert store.cache_entry("plain") == ([result], [])
    assert store.cache_entry("asked") == ([result], [("lib/deep.c", "abc")])
    assert store.cache_get("asked") == [result]
    assert store.cache_entry("none") is None


def test_job_keeps_the_connection_name(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job = store.create_job("x.xlsx", "RTE_Result", 0, [], [], connection="게이트웨이 B")
    old = store.create_job("x.xlsx", "RTE_Result", 0, [], [])

    assert store.job(job).connection == "게이트웨이 B"
    assert store.job(old).connection == ""
