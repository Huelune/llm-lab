"""worker가 작업에 기록된 연결로 요청한다."""

import json

from fake_llm import MODEL, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.config import Settings
from llm_lab.connections import ConnectionStore, connection_from_form
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import MISSING_CONNECTION, Worker

SOURCE = decode_source("calc.c", CALC_C)
FORM = {"client_kind": "openai", "base_url": "https://llm.test/v1", "api_key": "k", "model": MODEL}


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
        store,
        None,
        "",
        queued.append,
        connections=connections,
        base_settings=Settings(base_url="", api_key="", model=""),
        make_client=make,
    )
    return store, worker, connections, queued, sent_a, sent_b, made


def new_job(store, connection, settings=None):
    return store.create_job(
        "x.xlsx",
        "RTE_Result",
        0,
        [SOURCE],
        [NewRow(finding(), "calc.c", "pending")],
        connection=connection,
        settings=settings,
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


def test_retry_after_a_deleted_connection_uses_the_current_one(tmp_path):
    from fastapi.testclient import TestClient

    from llm_lab.web import create_app

    store, worker, connections, queued, sent_a, sent_b, made = setup(tmp_path)
    job = new_job(store, "A", settings={"connection": "A", "model": MODEL, "x": 1})
    worker.start(job)
    connections.select("B")
    connections.delete("A")
    for task in queued:
        task()
    queued.clear()
    client = TestClient(create_app(store, worker, connections), base_url="http://127.0.0.1")

    client.post(f"/results/{job}/retry")
    for task in queued:
        task()

    assert store.job(job).connection == "B"
    assert json.loads(store.job(job).settings) == {"connection": "B", "model": "model-b", "x": 1}
    assert len(sent_b) == 1 and sent_a == []
    assert store.rows(job)[0].state == "done"


def test_set_job_connection_records_the_name(tmp_path):
    store = Store(tmp_path / "fixer.db")
    job = new_job(store, "A")

    store.set_job_connection(job, "B", "model-b")

    assert store.job(job).connection == "B"
