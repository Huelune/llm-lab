"""worker의 정의 되묻기: 색인에서 찾아 보내고, 보낸 것을 작업에 남기고, 재사용 때 파일을 되살린다"""

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
DEEP_FIX = {
    "file": "lib/deep.c",
    "original": "    return v - 1;",
    "replacement": "    return v + 1;",
}


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


def test_need_is_answered_from_the_project_and_the_files_sent_stay_in_the_job(tmp_path):
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
    # 되물은 정의는 다른 묶음의 첫 요청에 섞이지 않게 작업에 남기지 않는다
    assert store.definitions(job_id) == []
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
