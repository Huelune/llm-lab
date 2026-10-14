"""작업·파일·행·캐시를 SQLite에 저장한다."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from llm_lab.csource import Definition
from llm_lab.fixer import FixResult
from llm_lab.polyspace import Finding
from llm_lab.sources import SourceFile

# 표 구조를 바꾸면 올리고 옛 DB를 옮기는 코드를 넣는다. PRAGMA user_version에 적는다.
# 2: jobs.source_root (소스를 읽은 폴더. 파일을 하나씩 올리던 예전 작업은 '')
# 3: jobs.kind·note·context (시트 종류, 작업 메모, 작업 정보 JSON), rows.grp (묶음)
# 4: definitions (분석 대상 파일이 쓰는 다른 곳의 정의)
SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    excel_name TEXT NOT NULL,
    sheet TEXT NOT NULL,
    skipped INTEGER NOT NULL,
    state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    source_root TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'rte',
    note TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS files (
    job_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    encoding TEXT NOT NULL,
    newline TEXT NOT NULL,
    bom INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS rows (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    finding TEXT NOT NULL,
    file_name TEXT,
    state TEXT NOT NULL,
    result TEXT,
    error TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS definitions (
    job_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    first INTEGER NOT NULL,
    last INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, name, file, first)
);
"""

# 예전 DB에 없을 수 있는 칸 (표, 칸, 정의). 켤 때 없으면 기본값으로 더한다.
ADDED_COLUMNS = [
    ("jobs", "source_root", "TEXT NOT NULL DEFAULT ''"),
    ("jobs", "kind", "TEXT NOT NULL DEFAULT 'rte'"),
    ("jobs", "note", "TEXT NOT NULL DEFAULT ''"),
    ("jobs", "context", "TEXT NOT NULL DEFAULT ''"),
    ("rows", "grp", "TEXT NOT NULL DEFAULT ''"),
]
# 사용자가 "멈춤"을 눌러 멈춘 작업의 메시지. 화면은 치명 오류로 멈춘 작업과 다르게 보인다.
USER_STOP = "사용자가 멈췄습니다."


class StoreError(Exception):
    """DB를 쓸 수 없음."""


@dataclass(frozen=True)
class Job:
    id: int
    created_at: str
    excel_name: str
    sheet: str
    skipped: int  # Gray Check 등 대상이 아니어서 뺀 행 수
    state: str  # running / done / stopped
    message: str
    source_root: str = ""  # 소스를 읽은 폴더. 파일을 하나씩 올리던 예전 작업은 ''
    kind: str = "rte"  # 시트 종류 (llm_lab.kinds). 스키마 3 전의 작업은 모두 RTE
    note: str = ""  # 작업 메모 한 줄 (조건 시트 요약, 뺀 행의 이유별 개수)
    context: str = ""  # 종류가 엑셀 전체에서 읽은 작업 정보 (JSON, 없으면 '')


@dataclass(frozen=True)
class Row:
    id: int
    job_id: int
    finding: Finding
    file_name: str | None  # 짝지은 업로드 파일 이름
    state: str  # pending / running / done / error / no_source
    result: FixResult | None
    error: str
    grp: str = ""  # 묶음 키. 키가 같은 행은 LLM에 함께 보낸다 ('' = 혼자)


@dataclass(frozen=True)
class NewRow:
    finding: Finding
    file_name: str | None
    state: str
    error: str = ""
    grp: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # 작업자 스레드 여럿이 쓰므로 쓰기는 한 번에 하나씩
        self._lock = threading.Lock()
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise StoreError(
                    f"{path}은(는) 새 버전 프로그램이 만든 DB입니다 "
                    f"(DB {version}, 프로그램 {SCHEMA_VERSION}). git pull로 코드를 받으세요."
                )
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            # 예전 버전 DB: 그 뒤에 생긴 칸을 더한다
            for table, column, definition in ADDED_COLUMNS:
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db() as db:
            yield db

    def create_job(
        self,
        excel_name: str,
        sheet: str,
        skipped: int,
        files: list[SourceFile],
        rows: list[NewRow],
        source_root: str = "",
        kind: str = "rte",
        note: str = "",
        context: str = "",
        definitions: Iterable[Definition] = (),
    ) -> int:
        state = "running" if any(row.state == "pending" for row in rows) else "done"
        with self._write() as db:
            cursor = db.execute(
                "INSERT INTO jobs (created_at, excel_name, sheet, skipped, state, source_root, "
                "kind, note, context) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), excel_name, sheet, skipped, state, source_root, kind, note, context),
            )
            job_id = cursor.lastrowid
            assert job_id is not None
            db.executemany(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (job_id, f.name, f.text, f.encoding, f.newline, int(f.bom), f.sha256)
                    for f in files
                ],
            )
            db.executemany(
                "INSERT INTO rows (job_id, finding, file_name, state, error, grp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        json.dumps(asdict(r.finding), ensure_ascii=False),
                        r.file_name,
                        r.state,
                        r.error,
                        r.grp,
                    )
                    for r in rows
                ],
            )
            db.executemany(
                "INSERT OR IGNORE INTO definitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(job_id, d.name, d.kind, d.file, d.first, d.last, d.text) for d in definitions],
            )
        return job_id

    def job(self, job_id: int) -> Job | None:
        with self._db() as db:
            found = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**dict(found)) if found else None

    def jobs(self, limit: int = 20) -> list[Job]:
        with self._db() as db:
            found = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Job(**dict(row)) for row in found]

    def recent_roots(self, limit: int = 10) -> list[str]:
        """최근에 쓴 소스 폴더 (가장 최근에 쓴 것부터, 중복 없이)."""
        with self._db() as db:
            found = db.execute(
                "SELECT source_root FROM jobs WHERE source_root != '' "
                "GROUP BY source_root ORDER BY MAX(id) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["source_root"] for row in found]

    def rows(self, job_id: int) -> list[Row]:
        with self._db() as db:
            found = db.execute("SELECT * FROM rows WHERE job_id = ? ORDER BY id", (job_id,))
            return [self._row(row) for row in found.fetchall()]

    def row(self, row_id: int) -> Row:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM rows WHERE id = ?", (row_id,)).fetchone())

    @staticmethod
    def _row(record: sqlite3.Row) -> Row:
        return Row(
            id=record["id"],
            job_id=record["job_id"],
            finding=Finding(**json.loads(record["finding"])),
            file_name=record["file_name"],
            state=record["state"],
            result=FixResult.from_json(record["result"]) if record["result"] else None,
            error=record["error"],
            grp=record["grp"],
        )

    def source(self, job_id: int, name: str) -> SourceFile:
        with self._db() as db:
            found = db.execute(
                "SELECT * FROM files WHERE job_id = ? AND name = ?", (job_id, name)
            ).fetchone()
        return SourceFile(
            name=found["name"],
            text=found["text"],
            encoding=found["encoding"],
            newline=found["newline"],
            bom=bool(found["bom"]),
            sha256=found["sha256"],
        )

    def sources(self, job_id: int) -> dict[str, SourceFile]:
        """작업에 저장한 모든 파일 (이름 → 원문)."""
        with self._db() as db:
            found = db.execute("SELECT * FROM files WHERE job_id = ?", (job_id,)).fetchall()
        return {
            record["name"]: SourceFile(
                name=record["name"],
                text=record["text"],
                encoding=record["encoding"],
                newline=record["newline"],
                bom=bool(record["bom"]),
                sha256=record["sha256"],
            )
            for record in found
        }

    def definitions(self, job_id: int) -> list[Definition]:
        """작업에 저장한 다른 곳의 정의. 이름·파일·줄 순서."""
        with self._db() as db:
            found = db.execute(
                "SELECT name, kind, file, first, last, text FROM definitions "
                "WHERE job_id = ? ORDER BY name, file, first",
                (job_id,),
            ).fetchall()
        return [Definition(**dict(record)) for record in found]

    def set_row(
        self, row_id: int, state: str, result: FixResult | None = None, error: str = ""
    ) -> None:
        with self._write() as db:
            db.execute(
                "UPDATE rows SET state = ?, result = ?, error = ? WHERE id = ?",
                (state, result.to_json() if result else None, error, row_id),
            )

    def claim_rows(self, row_ids: list[int]) -> bool:
        """묶음의 행이 모두 pending이면 한꺼번에 running으로 바꾼다.

        하나라도 pending이 아니면(다른 작업자가 먼저 가져갔으면) 아무것도 바꾸지 않고 False.
        """
        marks = ", ".join("?" * len(row_ids))
        with self._write() as db:
            pending = db.execute(
                f"SELECT COUNT(*) FROM rows WHERE id IN ({marks}) AND state = 'pending'", row_ids
            ).fetchone()[0]
            if pending != len(row_ids):
                return False
            db.execute(f"UPDATE rows SET state = 'running' WHERE id IN ({marks})", row_ids)
            return True

    def set_job(self, job_id: int, state: str, message: str = "") -> None:
        with self._write() as db:
            db.execute(
                "UPDATE jobs SET state = ?, message = ? WHERE id = ?", (state, message, job_id)
            )

    def stop(self, job_id: int) -> None:
        """처리 중인 작업을 멈춘다. 줄 서 있던 묶음은 건너뛰고, "이어서 처리"(retry)로 계속한다."""
        with self._write() as db:
            db.execute(
                "UPDATE jobs SET state = 'stopped', message = ? WHERE id = ? AND state = 'running'",
                (USER_STOP, job_id),
            )

    def finish_if_done(self, job_id: int) -> None:
        """남은 pending·running 행이 없으면 작업을 done으로 바꾼다.

        running 작업과, 사용자가 멈춘 작업(stopped, 메시지 USER_STOP)이 대상이다. 마지막 묶음이
        LLM 답을 기다리는 중에 멈추면 그 묶음이 끝난 뒤 남은 행이 없다. 그때 "이어서 처리"를
        남겨 두면 처리할 것이 없으므로 done으로 닫고 메시지를 지운다. 치명 오류로 멈춘 작업(다른
        메시지)은 그 묶음이 pending으로 돌아가 있으므로 이 규칙이 닫지 않는다.
        """
        with self._write() as db:
            db.execute(
                "UPDATE jobs SET state = 'done', message = '' WHERE id = ? "
                "AND (state = 'running' OR (state = 'stopped' AND message = ?)) "
                "AND NOT EXISTS "
                "(SELECT 1 FROM rows WHERE job_id = ? AND state IN ('pending', 'running'))",
                (job_id, USER_STOP, job_id),
            )

    def retry(self, job_id: int) -> None:
        """오류 행을 다시 pending으로 돌리고 작업을 running으로 되살린다."""
        with self._write() as db:
            db.execute(
                "UPDATE rows SET state = 'pending', error = '' "
                "WHERE job_id = ? AND state = 'error' AND file_name IS NOT NULL",
                (job_id,),
            )
            db.execute("UPDATE jobs SET state = 'running', message = '' WHERE id = ?", (job_id,))

    def recover(self) -> list[int]:
        """서비스를 켤 때: running 행을 pending으로 되돌리고 이어서 처리할 작업 id를 돌려준다."""
        with self._write() as db:
            db.execute("UPDATE rows SET state = 'pending' WHERE state = 'running'")
            found = db.execute("SELECT id FROM jobs WHERE state = 'running' ORDER BY id")
            return [row["id"] for row in found.fetchall()]

    def cache_get(self, key: str) -> list[FixResult] | None:
        """묶음의 행별 결과 (행 순서대로). 묶음 처리 전의 예전 값(결과 하나)은 없는 것으로 본다."""
        with self._db() as db:
            found = db.execute("SELECT result FROM cache WHERE key = ?", (key,)).fetchone()
        data = json.loads(found["result"]) if found else None
        if not isinstance(data, list):
            return None
        return [FixResult.from_dict(item) for item in data]

    def cache_put(self, key: str, results: list[FixResult]) -> None:
        text = json.dumps([asdict(result) for result in results], ensure_ascii=False)
        with self._write() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, text, _now()))
