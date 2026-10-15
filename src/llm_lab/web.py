"""LLM Test Advisor 웹 서비스 (llm-fix-server)."""

from __future__ import annotations

import argparse
import io
import json
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from llm_lab.config import ConfigError, Settings, load_settings
from llm_lab.connections import (
    ENV_NAME,
    ENV_READ_ONLY,
    ConnectionStore,
    check_connection,
    connection_from_form,
    connection_from_settings,
)
from llm_lab.csource import callers_for, enclosing_function, scan_project, used_definitions
from llm_lab.export import export_workbook
from llm_lab.folder_picker import PickerError, pick_folder
from llm_lab.kinds import KINDS, Kind
from llm_lab.pages import (
    APP_NAME,
    FAVICON_SVG,
    error_page,
    index_page,
    job_page,
    results_page,
    settings_page,
    summary_page,
)
from llm_lab.patch import FilePatch, PatchError, base_folder, build_patch, relative_path
from llm_lab.polyspace import Finding, Sheet, SheetError, read_sheet
from llm_lab.sources import DecodeError, Match, decode_source, load_from_folder
from llm_lab.store import Job, NewRow, Row, Store, StoreError
from llm_lab.worker import Worker, daemon_pool

# .env처럼 프로젝트 루트 기준. private/는 git에서 제외되어 있다. --profile NAME이면 private/NAME
DATA_DIR = Path("private")
DB_NAME = "fixer.db"
CONNECTIONS_NAME = "connections.json"  # 설정 페이지에서 추가한 LLM 연결 (키가 들어 있다)
# 행마다 LLM과 오간 대화를 Markdown 파일로 남기는 폴더
LOG_DIR_NAME = "llm-logs"
DB_PATH = DATA_DIR / DB_NAME
LOG_DIR = DATA_DIR / LOG_DIR_NAME

RECENT_JOBS = 10  # 첫 화면에 보여줄 최근 작업 수
LISTED_JOBS = 100  # 작업 목록 화면에 보여줄 작업 수


TITLE_MAX = 80


def _default_title(excel_name: str, kind_title: str) -> str:
    """작업 이름의 기본값. 엑셀 이름이 길면 줄여서 전체가 80자 안에 들게 한다."""
    if not excel_name:
        return kind_title
    tail = f" · {kind_title}"
    room = TITLE_MAX - len(tail)
    if len(excel_name) > room:
        excel_name = excel_name[: max(room - 1, 0)] + "…"
    return excel_name + tail


def _new_row(kind: Kind, finding: Finding, match: Match) -> NewRow:
    if match.source is None:
        return NewRow(finding, None, "no_source", match.reason)
    name = match.source.name
    return NewRow(finding, name, "pending", grp=kind.group_key(finding, name))


def precheck(
    excel: UploadFile | None, source_dir: str, kind: str, choice: list[str] | None
) -> dict[str, list[str]]:
    """분석 시작이 확인하는 것을 미리 본다: 종류·대상, 소스 폴더, 엑셀 시트와 대상 행, 찾은 파일.

    작업을 만들 때와 같은 함수(read_sheet, load_from_folder)를 쓰므로 숫자가 분석 시작과 같다.
    정의·호출자는 찾지 않는다(프로젝트 전체를 훑어 오래 걸린다).
    """
    errors: list[str] = []
    warnings: list[str] = []
    facts: list[str] = []
    result = {"errors": errors, "warnings": warnings, "facts": facts}
    sheet_kind = KINDS.get(kind)
    if sheet_kind is None:
        errors.append("분석할 시트 종류를 다시 고르세요.")
        return result
    prefix = f"{sheet_kind.name}:"
    selected = {v.removeprefix(prefix) for v in choice or [] if v.startswith(prefix)}
    selected &= set(sheet_kind.choices)
    if sheet_kind.choices and not selected:
        errors.append("분석할 대상을 하나 이상 고르세요.")
    root = _source_root(source_dir)
    if root is None:
        errors.append("소스 폴더 경로를 입력하세요.")
    elif not root.is_dir():
        errors.append("소스 폴더가 없습니다. 탐색기 주소창의 경로를 그대로 붙여넣으세요.")
    if excel is None or not excel.filename:
        errors.append("엑셀 파일을 고르세요.")
    if errors:
        return result
    try:
        sheet = read_sheet(io.BytesIO(excel.file.read()), sheet_kind, selected)
    except SheetError as exc:
        errors.append(str(exc))
        return result
    facts.append(f"시트 {sheet.sheet}: 대상 {len(sheet.findings)}행")
    if sheet.skipped:
        skipped = ", ".join(f"{reason} {count}" for reason, count in sheet.skipped.items())
        facts.append(f"제외: {skipped}")
    if sheet.prepared.note:
        facts.append(sheet.prepared.note)
    if not sheet.findings:
        errors.append("고른 대상에 해당하는 행이 없습니다. 대상이나 시트를 확인하세요.")
        return result
    matches, _ = load_from_folder(root, [f.file for f in sheet.findings])
    missing = {path for path, match in matches.items() if match.source is None}
    facts.append(f"소스 파일 {len(matches)}개 중 {len(matches) - len(missing)}개를 폴더에서 찾음")
    if missing:
        rows = sum(f.file in missing for f in sheet.findings)
        warnings.append(
            f"폴더에서 못 찾은 파일 {len(missing)}개 (대상 {rows}행은 '확인 필요'로 남습니다). "
            "프로젝트 최상위 폴더를 골랐는지 보세요."
        )
    return result


def _job_note(
    sheet: Sheet,
    definitions: int = 0,
    unreadable: int = 0,
    mode_label: str = "",
    callers: int = 0,
) -> str:
    """작업 메모 한 줄: 처리 방식, 종류가 읽은 메모, 뺀 행의 이유별 개수, 참고 정의 수."""
    parts = [mode_label] if mode_label else []
    if sheet.prepared.note:
        parts.append(sheet.prepared.note)
    if sheet.skipped:
        skipped = ", ".join(f"{reason} {count}" for reason, count in sheet.skipped.items())
        parts.append(f"제외: {skipped}")
    if definitions:
        parts.append(f"참고 정의 {definitions}개")
    if callers:
        parts.append(f"호출자 조각 {callers}개")
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


CROSS_SITE_REFUSED = "다른 사이트에서 보낸 요청이라 거부했습니다."
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _local(url: str) -> bool:
    """이 서버의 주소(http://127.0.0.1 또는 localhost, 포트는 무관)인지."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS
    except ValueError:
        return False


def _cross_site(headers: Mapping[str, str]) -> bool:
    """브라우저가 붙인 출처 헤더가 다른 사이트를 가리키는지. 헤더가 없으면(curl 등) 통과."""
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        return True
    origin = headers.get("origin")
    if origin is not None:
        return not _local(origin)
    referer = headers.get("referer")
    return referer is not None and not _local(referer)


def create_app(
    store: Store,
    worker: Worker,
    connections: ConnectionStore | None = None,
    base_settings: Settings | None = None,
) -> FastAPI:
    """connections가 없으면 위쪽 막대에 연결을 보이지 않고, 분석 시작도 연결을 확인하지 않는다."""
    app = FastAPI(title="llm-fix-server")
    # DNS 리바인딩으로 다른 사이트가 이 페이지(회사 소스·diff)를 읽지 못하게 Host를 제한한다
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def refuse_cross_site(request: Request, call_next):
        # Host 제한은 다른 사이트의 폼이 127.0.0.1로 보내는 POST(연결 바꿔치기, 분석 시작)를
        # 막지 못한다. 브라우저가 붙이는 출처 헤더를 보고 이 서버 밖에서 온 요청은 거부한다
        if request.method not in ("GET", "HEAD") and _cross_site(request.headers):
            return HTMLResponse(CROSS_SITE_REFUSED, status_code=403)
        return await call_next(request)

    def label() -> str | None:
        """위쪽 막대의 현재 연결 이름. 연결이 없으면 "", 연결 기능이 없으면 None."""
        if connections is None:
            return None
        current = connections.current()
        return current.name if current else ""

    def job_or_404(job_id: str) -> Job:
        job = store.job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업이 없습니다")
        return job

    def with_rows(jobs: list[Job]) -> list[tuple[Job, list[Row]]]:
        return [(job, store.rows(job.id)) for job in jobs]

    @app.get("/", response_class=HTMLResponse)
    def index(from_: Annotated[str | None, Query(alias="from")] = None) -> str:
        # ?from=작업 ID면 그 작업에 기록된 설정으로 양식을 채운다 (없는 작업이면 빈 양식)
        preset = store.job(from_) if from_ else None
        return index_page(
            with_rows(store.jobs(limit=RECENT_JOBS)),
            store.recent_roots(),
            connection=label(),
            preset_from=preset,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        # 브라우저가 자동으로 찾는 주소. 페이지의 data URI와 같은 그림이다
        return Response(FAVICON_SVG, media_type="image/svg+xml")

    @app.get("/results", response_class=HTMLResponse)
    def results() -> str:
        return results_page(with_rows(store.jobs(limit=LISTED_JOBS)), connection=label())

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

    @app.post("/check")
    def check_inputs(
        excel: Annotated[UploadFile | None, File()] = None,
        source_dir: Annotated[str, Form()] = "",
        kind: Annotated[str, Form()] = "rte",
        choice: Annotated[list[str] | None, Form()] = None,
    ) -> dict[str, list[str]]:
        """분석 시작 전 확인. 작업은 만들지 않고, 막는 문제·주의·확인된 개수를 돌려준다."""
        return precheck(excel, source_dir, kind, choice)

    @app.post("/results")
    def create_job(
        excel: Annotated[UploadFile, File()],
        source_dir: Annotated[str, Form()] = "",
        kind: Annotated[str, Form()] = "rte",
        choice: Annotated[list[str] | None, Form()] = None,
        mode: Annotated[list[str] | None, Form()] = None,
    ) -> Response:
        def fail(title: str, message: str) -> Response:
            return HTMLResponse(error_page(title, message, connection=label()), status_code=400)

        # 연결이 없으면 엑셀을 읽기 전에 멈춘다. 있으면 작업에 이름을 남겨 그 연결로 묻는다
        current = connections.current() if connections is not None else None
        if connections is not None and current is None:
            message = "설정에서 LLM 연결을 추가하고 고른 뒤 다시 시작하세요."
            return fail("LLM 연결이 없음", message)
        sheet_kind = KINDS.get(kind)
        if sheet_kind is None:
            return fail("알 수 없는 종류", f"분석할 시트 종류를 다시 고르세요: {kind}")
        # 체크 상자 값은 "종류:대상"이다. 고른 종류의 것만, 그 종류의 대상인 것만 쓴다
        prefix = f"{sheet_kind.name}:"
        picked = {value.removeprefix(prefix) for value in choice or [] if value.startswith(prefix)}
        selected = picked & set(sheet_kind.choices)
        if sheet_kind.choices and not selected:
            return fail("분석할 대상이 없음", "분석할 대상을 하나 이상 고르세요.")
        root = _source_root(source_dir)
        if root is None or not root.is_dir():
            message = (
                f"{root} 폴더가 없습니다." if root else "소스 폴더 경로를 입력하세요."
            ) + " 탐색기 주소창의 경로를 그대로 붙여넣으면 됩니다."
            return fail("소스 폴더를 찾을 수 없음", message)
        try:
            sheet = read_sheet(io.BytesIO(excel.file.read()), sheet_kind, selected)
        except SheetError as exc:
            return fail("엑셀을 읽을 수 없음", str(exc))
        # 엑셀에 나온 파일만 폴더에서 찾아 읽는다. 읽은 내용은 작업에 함께 저장해
        # 나중에 소스가 바뀌어도 결과와 패치는 분석 당시 기준으로 남는다.
        matches, files = load_from_folder(root, [f.file for f in sheet.findings])
        rows = [_new_row(sheet_kind, finding, matches[finding.file]) for finding in sheet.findings]
        used = {row.file_name for row in rows if row.file_name}
        files = [source for source in files if source.name in used]
        # 소스 폴더 전체의 정의 가운데 분석 대상 파일이 쓰는 것만 작업에 저장한다
        definitions, unreadable, callers = [], 0, []
        if sheet_kind.uses_definitions:
            found, unreadable, texts = scan_project(root)
            definitions = used_definitions(found, [source.text for source in files])
            if sheet_kind.sends_callers:
                # 행마다 검사 줄을 품은 함수를 정하고, 프로젝트 전체에서 그 함수의 호출부를 찾는다
                by_name = {source.name: source for source in files}
                targets: dict[str, str] = {}
                for new_row in rows:
                    source = by_name.get(new_row.file_name or "")
                    if source is None or new_row.finding.line is None:
                        continue
                    # 요청(make_request)과 같은 규칙: 검사 줄이 함수 밖이면 호출자를 찾지 않는다
                    name = enclosing_function(source.text, new_row.finding.line)
                    if name:
                        targets.setdefault(name, source.name)
                callers = callers_for(found, texts, targets)
            if sheet_kind.edits_definitions:
                # 고칠 수 있게 보낼 정의·호출자가 든 파일의 원문도 작업에 저장한다
                known = {source.name for source in files}
                # caller_ref(호출 없이 쓰인 곳의 수)는 파일이 없다
                shown = {d.file for d in [*definitions, *callers] if d.file}
                for name in sorted(shown - known):
                    try:
                        files.append(decode_source(name, (root / name).read_bytes()))
                    except DecodeError, OSError:
                        continue
        data = sheet.prepared.data
        # 처리 방식 라디오 값은 "종류:방식"이다. 고른 종류의 것이 없으면 첫 방식이 기본값이다
        mode_label = mode_value = ""
        if sheet_kind.modes:
            wanted = {
                value.removeprefix(prefix) for value in mode or [] if value.startswith(prefix)
            }
            chosen = next((m for m in sheet_kind.modes if m[0] in wanted), sheet_kind.modes[0])
            mode_value, mode_label = chosen[0], chosen[1]
            data = {**(data if isinstance(data, dict) else {}), "mode": chosen[0]}
        excel_name = excel.filename or ""
        job_id = store.create_job(
            excel_name,
            sheet.sheet,
            sum(sheet.skipped.values()),
            files,
            rows,
            source_root=str(root),
            kind=sheet_kind.name,
            note=_job_note(
                sheet,
                len({(d.file, d.first) for d in definitions}),
                unreadable,
                mode_label,
                sum(d.kind == "caller" for d in callers),
            ),
            definitions=[*definitions, *callers],
            context="" if data is None else json.dumps(data, ensure_ascii=False),
            connection=current.name if current else "",
            title=_default_title(excel_name, sheet_kind.title),
            settings={
                "connection": current.name if current else "",
                "model": current.model if current else worker.model,
                "kind": sheet_kind.name,
                "mode": mode_value,
                "choices": sorted(selected),
                "follow_ups": worker.follow_ups,
                "source_root": str(root),
                "excel": excel_name,
            },
        )
        worker.start(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.get("/results/{job_id}", response_class=HTMLResponse)
    def job_view(job_id: str) -> str:
        job = job_or_404(job_id)
        rows = store.rows(job_id)
        return job_page(job, rows, _base(job, rows), connection=label())

    @app.get("/results/{job_id}/results.xlsx")
    def export_excel(job_id: str) -> Response:
        job = job_or_404(job_id)
        return Response(
            export_workbook(job, store.rows(job_id)),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="job{job_id}-results.xlsx"'},
        )

    @app.get("/results/{job_id}/summary", response_class=HTMLResponse)
    def job_summary(job_id: str) -> str:
        return summary_page(job_or_404(job_id), store.rows(job_id))

    @app.post("/results/{job_id}/fixes.patch")
    def download_patch(job_id: str, row: Annotated[list[int] | None, Form()] = None) -> Response:
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
            page = error_page("패치를 만들 수 없음", message, connection=label())
            return HTMLResponse(page, status_code=400)
        base = _base(job, rows)
        by_file: dict[str, FilePatch] = {}
        for r in chosen:
            assert r.file_name is not None and r.result is not None
            for edit in r.result.edits:
                target = edit.file or r.file_name
                if target not in by_file:
                    source = store.source(job_id, target)
                    # 폴더에서 읽은 소스는 이름이 곧 폴더 기준 상대 경로다
                    path = source.name if job.source_root else relative_path(r.finding.file, base)
                    by_file[target] = FilePatch(path, source, [])
                by_file[target].edits.append((r.finding.row, replace(edit, file="")))
        try:
            data = build_patch(list(by_file.values()))
        except PatchError as exc:
            page = error_page("패치를 만들 수 없음", str(exc), connection=label())
            return HTMLResponse(page, status_code=409)
        return Response(
            data,
            media_type="text/x-diff",
            headers={"Content-Disposition": 'attachment; filename="fixes.patch"'},
        )

    @app.post("/results/{job_id}/rename")
    def rename(job_id: str, title: Annotated[str, Form()] = "") -> Response:
        job_or_404(job_id)
        title = title.strip()
        if not 1 <= len(title) <= 80:
            page = error_page(
                "이름을 바꿀 수 없음", "이름은 1~80자로 적으세요.", connection=label()
            )
            return HTMLResponse(page, status_code=400)
        store.rename_job(job_id, title)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.post("/results/{job_id}/stop")
    def stop(job_id: str) -> Response:
        job_or_404(job_id)
        store.stop(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.post("/results/{job_id}/retry")
    def retry(job_id: str) -> Response:
        job = job_or_404(job_id)
        # 작업의 연결이 지워졌으면 지금 고른 연결로 이어서 처리한다 (멈춤 안내가 시킨 대로)
        if connections is not None and job.connection and connections.get(job.connection) is None:
            current = connections.current()
            if current is not None:
                store.set_job_connection(job_id, current.name, current.model)
        store.retry(job_id)
        worker.start(job_id)
        return RedirectResponse(f"/results/{job_id}", status_code=303)

    @app.get("/api/results/{job_id}")
    def job_json(job_id: str) -> dict[str, Any]:
        job = job_or_404(job_id)
        rows = store.rows(job_id)
        base = _base(job, rows)
        return {**asdict(job), "base_folder": base, "rows": [_row_json(r) for r in rows]}

    if connections is not None:
        _add_settings_routes(app, connections, label, base_settings or worker.base_settings)
    return app


def _add_settings_routes(
    app: FastAPI,
    connections: ConnectionStore,
    label: Callable[[], str | None],
    base_settings: Settings | None,
) -> None:
    """설정 페이지. 저장·고르기·지우기는 성공하면 /settings로 돌아가고, 실패하면 이유를 보인
    설정 페이지를 바로 그린다."""

    checks: dict[str, str] = {}

    def settings_html(**kwargs: Any) -> str:
        current = connections.current()
        return settings_page(
            connections.list(),
            current.name if current else None,
            connections.notice(),
            connection=label(),
            checks=checks,
            **kwargs,
        )

    def back() -> Response:
        return RedirectResponse("/settings", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_view(edit: str = "") -> str:
        if not edit:
            return settings_html()
        target = connections.get(edit)
        if target is None:
            return settings_html(error="없는 연결입니다")
        if target.read_only:
            return settings_html(error=ENV_READ_ONLY)
        return settings_html(editing=edit)

    @app.post("/settings/save")
    async def settings_save(request: Request) -> Response:
        fields = {
            key: value for key, value in (await request.form()).items() if isinstance(value, str)
        }
        original = fields.get("original") or None
        try:
            keep = connections.get(original) if original else None
            connections.save(connection_from_form(fields, keep), original)
            checks.pop(original or fields.get("name", ""), None)
        except ConfigError as exc:
            return HTMLResponse(settings_html(form=fields, editing=original, error=str(exc)))
        return back()

    @app.post("/settings/select")
    def settings_select(name: Annotated[str, Form()] = "") -> Response:
        try:
            connections.select(name)
        except ConfigError as exc:
            return HTMLResponse(settings_html(error=str(exc)))
        return back()

    @app.post("/settings/check")
    def settings_check(name: Annotated[str, Form()] = "") -> Response:
        target = connections.get(name)
        if target is None:
            return HTMLResponse(settings_html(error="없는 연결입니다"))
        # 동기 def라 스레드 풀에서 돈다. 최대 10초 걸려도 다른 요청을 막지 않는다.
        checks[name] = check_connection(target, base_settings or Settings("", "", ""))
        return back()

    @app.post("/settings/delete")
    def settings_delete(name: Annotated[str, Form()] = "") -> Response:
        try:
            connections.delete(name)
            checks.pop(name, None)
        except ConfigError as exc:
            return HTMLResponse(settings_html(error=str(exc)))
        return back()


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


def _has_records(folder: Path) -> bool:
    return (folder / DB_NAME).is_file() or any((folder / LOG_DIR_NAME).glob("*.md"))


def _elsewhere_hint(data_dir: Path) -> None:
    """지울 기록이 없을 때, 다른 프로필이나 기본 위치에 기록이 있으면 알린다 (이름 없이)."""
    if data_dir == DATA_DIR:
        others = sum(1 for d in DATA_DIR.glob("*") if d.is_dir() and _has_records(d))
        if others:
            print(
                f"다른 프로필 {others}곳에 기록이 있습니다. "
                "서버를 켤 때 쓴 --profile 이름을 붙여 다시 실행하세요."
            )
    elif _has_records(DATA_DIR):
        print(
            "--profile 없이 켠 서버의 기록이 있습니다. 그 기록이면 --profile 없이 다시 실행하세요."
        )


def clear_records(data_dir: Path, what: str) -> int:
    """기록을 지우고 개수만 찍는다. 경로는 화면에 내지 않는다."""
    db_path = data_dir / DB_NAME
    try:
        if what == "cache":
            if not db_path.is_file():
                print("0 cached answers deleted")
                _elsewhere_hint(data_dir)
                return 0
            with closing(sqlite3.connect(db_path)) as db, db:
                count = db.execute("DELETE FROM cache").rowcount
            print(f"{count} cached answers deleted")
            return 0
        files = [*data_dir.glob(f"{DB_NAME}*"), *(data_dir / LOG_DIR_NAME).glob("*.md")]
        for path in files:
            path.unlink()
    except OSError, sqlite3.Error:
        print("지우지 못했습니다: 서버를 끈 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    print(f"{len(files)} files deleted")
    if not files:
        _elsewhere_hint(data_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm-fix-server", description=f"{APP_NAME} 서버")
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
    parser.add_argument(
        "--clear",
        choices=("all", "cache"),
        help="서버를 켜지 않고 기록을 지운다. all: 작업 목록·LLM 답·대화 기록, cache: LLM 답만 "
        "(--profile과 함께 쓰면 그 프로필의 기록). 서버를 끈 상태에서 실행한다",
    )
    args = parser.parse_args(argv)
    data_dir = DATA_DIR / args.profile if args.profile else DATA_DIR
    if args.clear:
        return clear_records(data_dir, args.clear)
    try:
        # 연결 값은 비어 있어도 된다 (설정 페이지에서 추가한다). workers·follow_ups는 여기서 읽는다
        settings = load_settings(args.profile, require_connection=False)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1
    try:
        store = Store(data_dir / DB_NAME)
    except StoreError as exc:
        print(f"DB 오류: {exc}", file=sys.stderr)
        return 1
    env = (
        connection_from_settings(ENV_NAME, settings, read_only=True) if settings.base_url else None
    )
    connections = ConnectionStore(data_dir / CONNECTIONS_NAME, env)
    if connections.error:
        print(f"{connections.notice()} 설정 페이지에서 다시 저장하세요.", file=sys.stderr)
    submit = daemon_pool(settings.workers)
    worker = Worker(
        store,
        None,
        settings.model,
        submit,
        log_dir=data_dir / LOG_DIR_NAME,
        follow_ups=settings.follow_ups,
        connections=connections,
        base_settings=settings,
    )
    worker.resume()
    url = f"http://127.0.0.1:{args.port}/"
    profile_note = f"   프로필: {args.profile}" if args.profile else ""
    print(f"\n  접속 주소: {url}{profile_note}   (끄려면 Ctrl+C)\n")
    if args.profile and not Path(f".env.{args.profile}").is_file():
        # 이름을 잘못 적었으면 빈 프로필로 켜진 것이다
        print(
            f"  프로필 설정 파일(.env.{args.profile})이 없어 저장된 연결만 씁니다. "
            "이름이 맞는지 확인하세요.\n"
        )
    if connections.current() is None:
        print("  LLM 연결이 없습니다: 접속 주소의 설정에서 추가하세요\n")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, args.port), daemon=True).start()
    # 끄면 처리 중이거나 줄 서 있는 행은 버린다. 다음에 켤 때 resume()이 이어서 처리한다.
    uvicorn.run(create_app(store, worker, connections), host="127.0.0.1", port=args.port)
    return 0
