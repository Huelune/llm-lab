"""scripts/peek_job.py: 작업이 정의를 왜 못 붙였는지 개수로만 센다 (회사 정보는 안 찍음)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from rte_fixtures import finding

from llm_lab.csource import scan_project, used_definitions
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peek_job.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peek_job", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


peek = _load()

CALC = b"int secret_func(int b)\n{\n    return 10 / Rte_Read_secret(b);\n}\n"


def project(tmp_path: Path) -> Path:
    """검사 파일 하나와, 줄바꿈이 섞인 RTE 파일, 인코딩을 못 읽는 RTE 파일, 읽히는 다른 파일."""
    root = tmp_path / "secret_proj"
    (root / "app").mkdir(parents=True)
    (root / "gen").mkdir()
    (root / "app" / "secret_calc.c").write_bytes(CALC)
    (root / "gen" / "Rte_secret.c").write_bytes(
        b"int Rte_Read_secret(int v)\r\n{\n    return v;\r\n}\r\n"
    )
    (root / "gen" / "Rte_secret_utf16.c").write_bytes("int Rte_x(void);\n".encode("utf-16"))
    (root / "app" / "secret_util.c").write_bytes(b"int secret_util(void)\n{\n    return 1;\n}\n")
    return root


def make_job(tmp_path: Path) -> Path:
    root = project(tmp_path)
    store = Store(tmp_path / "private" / "fixer.db")
    calc = decode_source("app/secret_calc.c", CALC)
    found, _, _ = scan_project(root)
    definitions = used_definitions(found, [calc.text])
    store.create_job(
        "secret.xlsx",
        "RTE_Result",
        0,
        [calc],
        [NewRow(finding(line=3, file=r"C:\secret\app\secret_calc.c"), calc.name, "pending")],
        source_root=str(root),
        kind="rte",
        definitions=definitions,
    )
    return tmp_path / "private" / "fixer.db"


def test_counts_why_nothing_was_attached(tmp_path):
    db = make_job(tmp_path)

    facts = dict(peek.facts(db, pattern="rte"))

    assert facts["stored_definitions"] == 1  # 검사 파일 자신의 함수뿐
    assert facts["stored_callers"] == 0
    assert facts["project_files"] == 4
    assert facts["unreadable_mixed_newlines"] == 1
    assert facts["unreadable_encoding"] == 1
    assert facts["pattern_files"] == 2
    assert facts["pattern_unreadable"] == 2
    assert facts["groups"] == 1
    assert facts["groups_with_definitions"] == 0
    assert facts["pattern_names_used"] == 1
    assert facts["pattern_names_defined"] == 0


def test_screen_is_numbers_only(tmp_path):
    db = make_job(tmp_path)

    screen = "\n".join(peek.summarize(db, pattern="rte"))

    assert "secret" not in screen.casefold()
    assert "rte_" not in screen.casefold()
    assert "줄바꿈이 섞여 못 읽은 파일" in screen
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118


def test_missing_database_or_folder_says_what_to_do(tmp_path):
    assert "작업 DB가 없습니다" in "\n".join(peek.summarize(tmp_path / "nope.db"))
    db = make_job(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "secret_proj")
    screen = "\n".join(peek.summarize(db, pattern="rte"))
    assert "소스 폴더를 찾지 못해 다시 훑지 않았습니다" in screen


def test_fresh_scan_shows_what_a_new_job_would_attach(tmp_path):
    # 예전 서버가 만든 작업처럼 정의 없이 저장하고, 지금 코드로 다시 훑으면 무엇이 붙는지 센다
    root = tmp_path / "secret_proj"
    (root / "app").mkdir(parents=True)
    (root / "gen").mkdir()
    calc_text = b"int secret_func(int b)\n{\n    return 10 / Rte_Read_ok(b);\n}\n"
    (root / "app" / "secret_calc.c").write_bytes(calc_text)
    (root / "gen" / "Rte_ok.c").write_bytes(b"int Rte_Read_ok(int v)\n{\n    return v;\n}\n")
    (root / "app" / "secret_main.c").write_bytes(
        b"int secret_main(void)\n{\n    return secret_func(3);\n}\n"
    )
    store = Store(tmp_path / "private" / "fixer.db")
    calc = decode_source("app/secret_calc.c", calc_text)
    store.create_job(
        "secret.xlsx",
        "RTE_Result",
        0,
        [calc],
        [NewRow(finding(line=3), calc.name, "pending")],
        source_root=str(root),
        kind="rte",
    )

    facts = dict(peek.facts(tmp_path / "private" / "fixer.db", pattern="rte"))

    assert facts["stored_definitions"] == 0
    assert facts["fresh_definitions"] == 2
    assert facts["fresh_pattern_names_defined"] == 1
    assert facts["fresh_callers"] == 1
    screen = "\n".join(peek.summarize(tmp_path / "private" / "fixer.db", pattern="rte"))
    assert "지금 코드로 작업을 새로 만들면" in screen
    assert "secret" not in screen.casefold()


def test_job_is_found_by_an_id_prefix(tmp_path):
    db = make_job(tmp_path)
    job = Store(db).jobs(limit=1)[0].id

    for prefix in (job[:8], job[:4].upper(), job):
        screen = "\n".join(peek.summarize(db, job_id=prefix, pattern="rte"))
        assert f"작업 {job[:8]}: 저장된 파일" in screen
        assert job not in screen
        assert "secret" not in screen.casefold()


def test_prefix_that_matches_several_or_none_says_what_to_do(tmp_path):
    db = make_job(tmp_path)
    store = Store(db)
    first = store.jobs(limit=1)[0].id
    # 앞 4자가 같은 작업을 직접 넣어 모호한 앞부분을 만든다
    import sqlite3

    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO jobs (id, created_at, excel_name, sheet, skipped, state) "
            "VALUES (?, 'x', 'x', 'x', 0, 'done')",
            (first[:6] + "ffffff-0000-4000-8000-000000000000"[: 36 - 6],),
        )
    assert peek.summarize(db, job_id=first[:4]) == [peek.AMBIGUOUS]
    assert peek.summarize(db, job_id="zzzz") == [peek.NOT_FOUND]
    assert peek.summarize(db, job_id=first[:3]) == [peek.NOT_FOUND]


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
