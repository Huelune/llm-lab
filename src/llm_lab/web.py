"""Polyspace 수정 제안 웹 서비스 (llm-fix-server)."""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from llm_lab.client import get_client
from llm_lab.config import ConfigError, load_settings
from llm_lab.csource import scan_folder, used_definitions
from llm_lab.folder_picker import PickerError, pick_folder
from llm_lab.kinds import KINDS, Kind
from llm_lab.pages import FAVICON_SVG, error_page, index_page, job_page, results_page
from llm_lab.patch import FilePatch, PatchError, base_folder, build_patch, relative_path
from llm_lab.polyspace import Finding, Sheet, SheetError, read_sheet
from llm_lab.sources import Match, load_from_folder
from llm_lab.store import Job, NewRow, Row, Store, StoreError
from llm_lab.worker import Worker, daemon_pool

# .env처럼 프로젝트 루트 기준. private/는 git에서 제외되어 있다. --profile NAME이면 private/NAME
DATA_DIR = Path("private")
DB_NAME = "fixer.db"
# 행마다 LLM과 오간 대화를 Markdown 파일로 남기는 폴더
LOG_DIR_NAME = "llm-logs"
DB_PATH = DATA_DIR / DB_NAME
LOG_DIR = DATA_DIR / LOG_DIR_NAME
WORKERS = 2
RECENT_JOBS = 10  # 첫 화면에 보여줄 최근 작업 수
LISTED_JOBS = 100  # 작업 목록 화면에 보여줄 작업 수


def _new_row(kind: Kind, finding: Finding, match: Match) -> NewRow:
    if match.source is None:
        return NewRow(finding, None, "no_source", match.reason)
    name = match.source.name
    return NewRow(finding, name, "pending", grp=kind.group_key(finding, name))


def _job_note(sheet: Sheet, definitions: int = 0, unreadable: int = 0, mode_label: str = "") -> str:
    """작업 메모 한 줄: 처리 방식, 종류가 읽은 메모, 뺀 행의 이유별 개수, 참고 정의 수."""
    parts = [mode_label] if mode_label else []
    if sheet.prepared.note:
        parts.append(sheet.prepared.note)
    if sheet.skipped:
        skipped = ", ".join(f"{reason} {count}" for reason, count in sheet.skipped.items())
        parts.append(f"제외: {skipped}")
    if definitions:
        parts.append(f"참고 정의 {definitions}개")
    if unreadable:
        parts.append(f"못 읽은 파일 {unreadable}개")
    return " · ".join(parts)


def _base(job: Job, rows: list[Row]) -> str | None:
    """패치 경로의 기준 폴더. 소스 폴더를 지정한 작업은 그 폴더, 파일을 하나씩 올리던
    예전 작업은 엑셀 경로들의 공통 폴더."""
    return job.source_root or base_folder(row.finding.file for row in rows if row.file_name)


def _source_root(text: str) -> Path | None:
    """화면에 붙여넣은 소스 폴더 경로. 탐색기 '경로로 복사'의 따옴표는 뗀다."""
    cleaned = text.strip().strip('"').strip()
    return Path(cleaned).absolute() if cleaned else None


def _row_json(row: Row) -> dict[str, Any]:
    return {
        "id": row.id,
        "state": row.state,
        "error": row.error,
        "group": row.grp,
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
        return index_page(with_rows(store.jobs(limit=RECENT_JOBS)), store.recent_roots())

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        # 브라우저가 자동으로 찾는 주소. 페이지의 data URI와 같은 그림이다
        return Response(FAVICON_SVG, media_type="image/svg+xml")

    @app.get("/results", response_class=HTMLResponse)
    def results() -> str:
        return results_page(with_rows(store.jobs(limit=LISTED_JOBS)))

    picking = threading.Lock()  # 폴더 선택 창은 한 번에 하나만

    @app.post("/pick-folder")
    def choose_folder(request: Request, initial: Annotated[str, Form()] = "") -> Response:
        # 이 페이지의 스크립트만 붙이는 헤더. 다른 사이트가 이 헤더를 붙여 보내려면 브라우저가
        # 먼저 허락(CORS)을 묻는데 이 서버는 허락하지 않으므로, 몰래 창을 띄울 수 없다.
        if request.headers.get("x-folder-picker") != "1":
            return JSONResponse({"error": "허용되지 않은 요청입니다"}, status_code=403)
        if not picking.acquire(blocking=False):
            message = "이미 폴더 선택 창이 열려 있습니다. 작업 표시줄을 확인하세요"
            return JSONResponse({"error": message})
        try:
            start = _source_root(initial)
            chosen = pick_folder(str(start) if start and start.is_dir() else "")
        except PickerError as exc:
            return JSONResponse({"error": str(exc)})
        finally:
            picking.release()
        return JSONResponse({"path": chosen})

    @app.post("/results")
    def create_job(
        excel: Annotated[UploadFile, File()],
        source_dir: Annotated[str, Form()] = "",
        kind: Annotated[str, Form()] = "rte",
        choice: Annotated[list[str] | None, Form()] = None,
        mode: Annotated[list[str] | None, Form()] = None,
    ) -> Response:
        sheet_kind = KINDS.get(kind)
        if sheet_kind is None:
            message = f"분석할 시트 종류를 다시 고르세요: {kind}"
            return HTMLResponse(error_page("알 수 없는 종류", message), status_code=400)
        # 체크 상자 값은 "종류:대상"이다. 고른 종류의 것만, 그 종류의 대상인 것만 쓴다
        prefix = f"{sheet_kind.name}:"
        picked = {value.removeprefix(prefix) for value in choice or [] if value.startswith(prefix)}
        selected = picked & set(sheet_kind.choices)
        if sheet_kind.choices and not selected:
            message = "분석할 대상을 하나 이상 고르세요."
            return HTMLResponse(error_page("분석할 대상이 없음", message), status_code=400)
        root = _source_root(source_dir)
        if root is None or not root.is_dir():
            message = (
                f"{root} 폴더가 없습니다." if root else "소스 폴더 경로를 입력하세요."
            ) + " 탐색기 주소창의 경로를 그대로 붙여넣으면 됩니다."
            return HTMLResponse(error_page("소스 폴더를 찾을 수 없음", message), status_code=400)
        try:
            sheet = read_sheet(io.BytesIO(excel.file.read()), sheet_kind, selected)
        except SheetError as exc:
            return HTMLResponse(error_page("엑셀을 읽을 수 없음", str(exc)), status_code=400)
        # 엑셀에 나온 파일만 폴더에서 찾아 읽는다. 읽은 내용은 작업에 함께 저장해
        # 나중에 소스가 바뀌어도 결과와 패치는 분석 당시 기준으로 남는다.
        matches, files = load_from_folder(root, [f.file for f in sheet.findings])
        rows = [_new_row(sheet_kind, finding, matches[finding.file]) for finding in sheet.findings]
        used = {row.file_name for row in rows if row.file_name}
        files = [source for source in files if source.name in used]
        # 소스 폴더 전체의 정의 가운데 분석 대상 파일이 쓰는 것만 작업에 저장한다
        definitions, unreadable = [], 0
        if sheet_kind.uses_definitions:
            found, unreadable = scan_folder(root)
            definitions = used_definitions(found, [source.text for source in files])
        data = sheet.prepared.data
        # 처리 방식 라디오 값은 "종류:방식"이다. 고른 종류의 것이 없으면 첫 방식이 기본값이다
        mode_label = ""
        if sheet_kind.modes:
            wanted = {
                value.removeprefix(prefix) for value in mode or [] if value.startswith(prefix)
            }
            chosen = next((m for m in sheet_kind.modes if m[0] in wanted), sheet_kind.modes[0])
            mode_label = chosen[1]
            data = {**(data if isinstance(data, dict) else {}), "mode": chosen[0]}
        job_id = store.create_job(
            excel.filename or "",
            sheet.sheet,
            sum(sheet.skipped.values()),
            files,
            rows,
            source_root=str(root),
            kind=sheet_kind.name,
            note=_job_note(
                sheet, len({(d.file, d.first) for d in definitions}), unreadable, mode_label
            ),
            definitions=definitions,
            context="" if data is None else json.dumps(data, ensure_ascii=False),
        )
        worker.start(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.get("/results/{job_id}", response_class=HTMLResponse)
    def job_view(job_id: int) -> str:
        job = job_or_404(job_id)
        rows = store.rows(job_id)
        return job_page(job, rows, _base(job, rows))

    @app.post("/results/{job_id}/fixes.patch")
    def download_patch(job_id: int, row: Annotated[list[int] | None, Form()] = None) -> Response:
        job = job_or_404(job_id)
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
        base = _base(job, rows)
        by_file: dict[str, FilePatch] = {}
        for r in chosen:
            assert r.file_name is not None and r.result is not None
            if r.file_name not in by_file:
                source = store.source(job_id, r.file_name)
                # 폴더에서 읽은 소스는 이름이 곧 폴더 기준 상대 경로다
                path = source.name if job.source_root else relative_path(r.finding.file, base)
                by_file[r.file_name] = FilePatch(path, source, [])
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

    @app.post("/results/{job_id}/stop")
    def stop(job_id: int) -> Response:
        job_or_404(job_id)
        store.stop(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

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
        base = _base(job, rows)
        return {**asdict(job), "base_folder": base, "rows": [_row_json(r) for r in rows]}

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
        prog="llm-fix-server", description="Polyspace 수정 제안 서비스"
    )
    parser.add_argument("--port", type=int, default=8000, help="접속 포트 (기본 8000)")
    parser.add_argument(
        "--no-browser", action="store_true", help="서버를 켤 때 브라우저를 자동으로 열지 않음"
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="설정은 .env.NAME, 작업 DB와 대화 기록은 private/NAME을 쓴다 "
        "(같은 폴더에서 서버를 하나 더 띄울 때)",
    )
    args = parser.parse_args(argv)
    data_dir = DATA_DIR / args.profile if args.profile else DATA_DIR
    try:
        settings = load_settings(args.profile)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1
    try:
        store = Store(data_dir / DB_NAME)
    except StoreError as exc:
        print(f"DB 오류: {exc}", file=sys.stderr)
        return 1
    submit = daemon_pool(WORKERS)
    worker = Worker(
        store, get_client(settings), settings.model, submit, log_dir=data_dir / LOG_DIR_NAME
    )
    worker.resume()
    url = f"http://127.0.0.1:{args.port}/"
    profile_note = f"   프로필: {args.profile}" if args.profile else ""
    print(f"\n  접속 주소: {url}{profile_note}   (끄려면 Ctrl+C)\n")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, args.port), daemon=True).start()
    # 끄면 처리 중이거나 줄 서 있는 행은 버린다. 다음에 켤 때 resume()이 이어서 처리한다.
    uvicorn.run(create_app(store, worker), host="127.0.0.1", port=args.port)
    return 0
