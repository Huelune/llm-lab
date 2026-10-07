"""결과 화면 HTML. 페이지가 몇 개뿐이라 템플릿 엔진 없이 문자열로 만든다."""

from __future__ import annotations

from collections import Counter
from html import escape

from llm_lab.patch import APPLY_COMMAND
from llm_lab.sources import base_name
from llm_lab.store import Job, Row

JOB_STATES = {"running": "처리 중", "done": "완료", "stopped": "멈춤"}
ROW_STATES = {
    "pending": "대기",
    "running": "처리 중",
    "done": "완료",
    "error": "오류",
    "no_source": "소스 없음",
}
DECISIONS = {"fix": "수정", "no_fix": "수정 불필요"}

STYLE = """
body { font-family: sans-serif; margin: 24px; color: #222; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #f3f3f3; }
.Red { background: #f8d0d0; } .Orange { background: #fbe3c4; }
pre { margin: 4px 0; padding: 6px; background: #fafafa; overflow-x: auto; }
.add { background: #dff5dd; } .del { background: #f9dcdc; } .hunk { color: #777; }
.note { color: #555; } .error { color: #b00020; }
code { background: #eee; padding: 1px 4px; }
"""


def page(title: str, body: str, *, refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">{meta}'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><h1>{escape(title)}</h1>{body}</body></html>"
    )


def error_page(title: str, message: str) -> str:
    return page(title, f'<p class="error">{escape(message)}</p><p><a href="/">처음으로</a></p>')


def index_page(jobs: list[Job]) -> str:
    form = (
        '<form method="post" action="/jobs" enctype="multipart/form-data">'
        '<p>Polyspace 결과 엑셀: <input type="file" name="excel" accept=".xlsx" required></p>'
        '<p>소스 파일(여러 개): <input type="file" name="sources" multiple required></p>'
        '<p><button type="submit">분석 시작</button></p></form>'
    )
    items = "".join(
        f'<li><a href="/jobs/{job.id}">작업 {job.id}</a> {escape(job.created_at)} '
        f"{escape(job.excel_name)} - {JOB_STATES.get(job.state, job.state)}</li>"
        for job in jobs
    )
    recent = f"<h2>최근 작업</h2><ul>{items}</ul>" if items else ""
    return page("Polyspace RTE 수정 제안", form + recent)


def _diff(diff: str) -> str:
    lines = []
    for line in diff.split("\n"):
        kind = ""
        if line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+") and not line.startswith("+++"):
            kind = "add"
        elif line.startswith("-") and not line.startswith("---"):
            kind = "del"
        lines.append(f'<span class="{kind}">{escape(line)}</span>' if kind else escape(line))
    return "<pre>" + "\n".join(lines) + "</pre>"


def _summary(job: Job, rows: list[Row]) -> str:
    colors = Counter(row.finding.color for row in rows)
    states = Counter(row.state for row in rows)
    decisions = Counter(row.result.decision for row in rows if row.result)
    parts = [
        f"Red {colors['Red']} · Orange {colors['Orange']} · 제외(Gray 등) {job.skipped}",
        " · ".join(f"{label} {states[key]}" for key, label in ROW_STATES.items() if states[key]),
        " · ".join(f"{label} {decisions[key]}" for key, label in DECISIONS.items()),
    ]
    status = JOB_STATES.get(job.state, job.state)
    if job.state == "running":
        status += " (5초마다 새로 고침)"
    elif job.state == "stopped":
        status += f": {job.message}"
    return (
        f"<p>{escape(job.excel_name)} / 시트 {escape(job.sheet)} / 상태: {escape(status)}</p>"
        + "".join(f'<p class="note">{escape(part)}</p>' for part in parts if part)
    )


def _apply_guide(base: str | None) -> str:
    if base is None:
        folder = "소스 파일이 있는 폴더"
    elif base == "":
        folder = "엑셀 File 경로가 시작되는 폴더"
    else:
        folder = base.replace("/", "\\")
    return (
        f"<p>고른 수정을 패치로 받아 <b>{escape(folder)}</b>에서 실행하세요: "
        f"<code>{APPLY_COMMAND} --check fixes.patch</code> 다음 "
        f"<code>{APPLY_COMMAND} fixes.patch</code></p>"
    )


def _row(row: Row, selectable: bool) -> str:
    finding = row.finding
    can_pick = selectable and row.result is not None and row.result.decision == "fix"
    pick = f'<input type="checkbox" name="row" value="{row.id}">' if can_pick else ""
    decision = DECISIONS.get(row.result.decision, "") if row.result else ""
    where = f"{base_name(finding.file)}:{finding.line if finding.line is not None else '?'}"
    head = (
        f"<tr><td>{pick}</td><td>{finding.row}</td>"
        f'<td class="{finding.color}">{escape(finding.type)}</td>'
        f"<td>{escape(finding.check)}</td><td>{escape(where)}</td>"
        f"<td>{escape(finding.function)}</td><td>{ROW_STATES.get(row.state, row.state)}</td>"
        f"<td>{decision}</td></tr>"
    )
    details = [f"<div>{escape(finding.detail)}</div>"]
    if row.result:
        details.append(f"<div><b>이유:</b> {escape(row.result.reason)}</div>")
        if row.result.diff:
            details.append(_diff(row.result.diff))
    if row.error:
        details.append(f'<div class="error">{escape(row.error)}</div>')
    return head + f'<tr><td></td><td colspan="7">{"".join(details)}</td></tr>'


def job_page(job: Job, rows: list[Row], base: str | None) -> str:
    finished = job.state != "running"
    has_fix = any(row.result and row.result.decision == "fix" for row in rows)
    table = (
        "<table><tr><th>선택</th><th>엑셀 행</th><th>TYPE</th><th>check</th><th>위치</th>"
        "<th>Function</th><th>상태</th><th>판단</th></tr>"
        + "".join(_row(row, finished) for row in rows)
        + "</table>"
    )
    body = _summary(job, rows)
    if finished and has_fix:
        body += _apply_guide(base)
        table = (
            f'<form method="post" action="/jobs/{job.id}/patch">'
            '<p><button type="submit">고른 수정을 패치로 내려받기</button></p>'
            f"{table}</form>"
        )
    if finished and (job.state == "stopped" or any(row.state == "error" for row in rows)):
        body += (
            f'<form method="post" action="/jobs/{job.id}/retry">'
            '<button type="submit">오류 행 다시 시도</button></form>'
        )
    if not rows:
        body += "<p>고칠 행 없음 (Red/Orange 행이 없습니다)</p>"
    body += table + '<p><a href="/">처음으로</a></p>'
    return page(f"작업 {job.id}", body, refresh=not finished)
