"""작업·파일·행·캐시를 SQLite에 저장한다."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from llm_lab.fixer import FixResult
from llm_lab.polyspace import Finding
from llm_lab.sources import SourceFile

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    excel_name TEXT NOT NULL,
    sheet TEXT NOT NULL,
    skipped INTEGER NOT NULL,
    state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
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
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Job:
    id: int
    created_at: str
    excel_name: str
    sheet: str
    skipped: int  # Gray Check 등 대상이 아니어서 뺀 행 수
    state: str  # running / done / stopped
    message: str


@dataclass(frozen=True)
class Row:
    id: int
    job_id: int
    finding: Finding
    file_name: str | None  # 짝지은 업로드 파일 이름
    state: str  # pending / running / done / error / no_source
    result: FixResult | None
    error: str


@dataclass(frozen=True)
class NewRow:
    finding: Finding
    file_name: str | None
    state: str
    error: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # 작업자 스레드 여럿이 쓰므로 쓰기는 한 번에 하나씩
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

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
        self, excel_name: str, sheet: str, skipped: int, files: list[SourceFile], rows: list[NewRow]
    ) -> int:
        state = "running" if any(row.state == "pending" for row in rows) else "done"
        with self._write() as db:
            cursor = db.execute(
                "INSERT INTO jobs (created_at, excel_name, sheet, skipped, state) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), excel_name, sheet, skipped, state),
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
                "INSERT INTO rows (job_id, finding, file_name, state, error) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        json.dumps(asdict(r.finding), ensure_ascii=False),
                        r.file_name,
                        r.state,
                        r.error,
                    )
                    for r in rows
                ],
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

    def set_row(
        self, row_id: int, state: str, result: FixResult | None = None, error: str = ""
    ) -> None:
        with self._write() as db:
            db.execute(
                "UPDATE rows SET state = ?, result = ?, error = ? WHERE id = ?",
                (state, result.to_json() if result else None, error, row_id),
            )

    def set_job(self, job_id: int, state: str, message: str = "") -> None:
        with self._write() as db:
            db.execute(
                "UPDATE jobs SET state = ?, message = ? WHERE id = ?", (state, message, job_id)
            )

    def finish_if_done(self, job_id: int) -> None:
        """남은 pending·running 행이 없으면 running 작업을 done으로 바꾼다."""
        with self._write() as db:
            db.execute(
                "UPDATE jobs SET state = 'done' WHERE id = ? AND state = 'running' AND NOT EXISTS "
                "(SELECT 1 FROM rows WHERE job_id = ? AND state IN ('pending', 'running'))",
                (job_id, job_id),
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

    def cache_get(self, key: str) -> FixResult | None:
        with self._db() as db:
            found = db.execute("SELECT result FROM cache WHERE key = ?", (key,)).fetchone()
        return FixResult.from_json(found["result"]) if found else None

    def cache_put(self, key: str, result: FixResult) -> None:
        with self._write() as db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, result.to_json(), _now())
            )
