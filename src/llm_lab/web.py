"""Polyspace RTE 수정 제안 웹 서비스 (llm-fix-server)."""

from __future__ import annotations

import argparse
import io
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_lab.client import get_client
from llm_lab.config import ConfigError, load_settings
from llm_lab.pages import error_page, index_page, job_page, results_page
from llm_lab.patch import FilePatch, PatchError, base_folder, build_patch, relative_path
from llm_lab.polyspace import Finding, SheetError, read_rte
from llm_lab.sources import DecodeError, Match, SourceFile, decode_source, match_sources
from llm_lab.store import Job, NewRow, Row, Store, StoreError
from llm_lab.worker import Worker, daemon_pool

# .env처럼 프로젝트 루트 기준. private/는 git에서 제외되어 있다
DB_PATH = Path("private/fixer.db")
# 행마다 LLM과 오간 대화를 Markdown 파일로 남기는 폴더
LOG_DIR = Path("private/llm-logs")
WORKERS = 2
RECENT_JOBS = 10  # 첫 화면에 보여줄 최근 작업 수
LISTED_JOBS = 100  # 작업 목록 화면에 보여줄 작업 수


def _new_row(finding: Finding, match: Match) -> NewRow:
    if match.source is None:
        return NewRow(finding, None, "no_source", match.reason)
    return NewRow(finding, match.source.name, "pending")


def _base(rows: list[Row]) -> str | None:
    return base_folder(row.finding.file for row in rows if row.file_name)


def _row_json(row: Row) -> dict[str, Any]:
    return {
        "id": row.id,
        "state": row.state,
        "error": row.error,
        "finding": asdict(row.finding),
        "decision": row.result.decision if row.result else None,
        "reason": row.result.reason if row.result else None,
        "diff": row.result.diff if row.result else None,
    }


def create_app(store: Store, worker: Worker) -> FastAPI:
    app = FastAPI(title="llm-fix-server")
    # DNS 리바인딩으로 다른 사이트가 이 페이지(회사 소스·diff)를 읽지 못하게 Host를 제한한다
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def job_or_404(job_id: int) -> Job:
        job = store.job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업이 없습니다")
        return job

    def with_rows(jobs: list[Job]) -> list[tuple[Job, list[Row]]]:
        return [(job, store.rows(job.id)) for job in jobs]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return index_page(with_rows(store.jobs(limit=RECENT_JOBS)))

    @app.get("/results", response_class=HTMLResponse)
    def results() -> str:
        return results_page(with_rows(store.jobs(limit=LISTED_JOBS)))

    @app.post("/results")
    def create_job(
        excel: Annotated[UploadFile, File()], sources: Annotated[list[UploadFile], File()]
    ) -> Response:
        try:
            sheet = read_rte(io.BytesIO(excel.file.read()))
        except SheetError as exc:
            return HTMLResponse(error_page("엑셀을 읽을 수 없음", str(exc)), status_code=400)
        decoded: list[SourceFile] = []
        undecodable: dict[str, str] = {}
        for upload in sources:
            if not upload.filename:
                continue  # 파일을 고르지 않은 칸
            try:
                decoded.append(decode_source(upload.filename, upload.file.read()))
            except DecodeError as exc:
                undecodable[upload.filename] = str(exc)
        matches = match_sources([f.file for f in sheet.findings], decoded, undecodable)
        rows = [_new_row(finding, matches[finding.file]) for finding in sheet.findings]
        used = {row.file_name for row in rows if row.file_name}
        files = [source for source in decoded if source.name in used]
        job_id = store.create_job(excel.filename or "", sheet.sheet, sheet.skipped, files, rows)
        worker.start(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.get("/results/{job_id}", response_class=HTMLResponse)
    def job_view(job_id: int) -> str:
        job = job_or_404(job_id)
        rows = store.rows(job_id)
        return job_page(job, rows, _base(rows))

    @app.post("/results/{job_id}/fixes.patch")
    def download_patch(job_id: int, row: Annotated[list[int] | None, Form()] = None) -> Response:
        job_or_404(job_id)
        rows = store.rows(job_id)
        wanted = set(row or [])
        chosen = [
            r
            for r in rows
            if r.id in wanted and r.file_name and r.result and r.result.decision == "fix"
        ]
        if not chosen:
            message = "적용할 수정을 하나 이상 고르세요."
            return HTMLResponse(error_page("패치를 만들 수 없음", message), status_code=400)
        base = _base(rows)
        by_file: dict[str, FilePatch] = {}
        for r in chosen:
            assert r.file_name is not None and r.result is not None
            if r.file_name not in by_file:
                source = store.source(job_id, r.file_name)
                by_file[r.file_name] = FilePatch(relative_path(r.finding.file, base), source, [])
            by_file[r.file_name].edits.extend((r.finding.row, edit) for edit in r.result.edits)
        try:
            data = build_patch(list(by_file.values()))
        except PatchError as exc:
            return HTMLResponse(error_page("패치를 만들 수 없음", str(exc)), status_code=409)
        return Response(
            data,
            media_type="text/x-diff",
            headers={"Content-Disposition": 'attachment; filename="fixes.patch"'},
        )

    @app.post("/results/{job_id}/retry")
    def retry(job_id: int) -> Response:
        job_or_404(job_id)
        store.retry(job_id)
        worker.start(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.get("/api/results/{job_id}")
    def job_json(job_id: int) -> dict[str, Any]:
        job = job_or_404(job_id)
        rows = store.rows(job_id)
        return {**asdict(job), "base_folder": _base(rows), "rows": [_row_json(r) for r in rows]}

    return app


def open_when_ready(url: str, port: int, timeout: float = 15.0) -> None:
    """서버가 포트를 열면 브라우저로 url을 연다. 시간 안에 안 열리면 조용히 넘어간다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        return
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-fix-server", description="Polyspace RTE 수정 제안 서비스"
    )
    parser.add_argument("--port", type=int, default=8000, help="접속 포트 (기본 8000)")
    parser.add_argument(
        "--no-browser", action="store_true", help="서버를 켤 때 브라우저를 자동으로 열지 않음"
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1
    try:
        store = Store(DB_PATH)
    except StoreError as exc:
        print(f"DB 오류: {exc}", file=sys.stderr)
        return 1
    submit = daemon_pool(WORKERS)
    worker = Worker(store, get_client(settings), settings.model, submit, log_dir=LOG_DIR)
    worker.resume()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  접속 주소: {url}   (끄려면 Ctrl+C)\n")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, args.port), daemon=True).start()
    # 끄면 처리 중이거나 줄 서 있는 행은 버린다. 다음에 켤 때 resume()이 이어서 처리한다.
    uvicorn.run(create_app(store, worker), host="127.0.0.1", port=args.port)
    return 0
