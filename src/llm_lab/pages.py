"""서비스 화면 HTML. 페이지가 몇 개뿐이라 템플릿 엔진 없이 문자열로 만든다.

외부 CSS·스크립트·글꼴을 쓰지 않는다 (브라우저도 바깥으로 요청하지 않게).
"""

from __future__ import annotations

from collections import Counter
from html import escape

from llm_lab.patch import APPLY_COMMAND
from llm_lab.sources import base_name
from llm_lab.store import Job, Row

APP_NAME = "Polyspace RTE 수정 제안"
JOB_STATES = {"running": "처리 중", "done": "완료", "stopped": "멈춤"}
JOB_BADGES = {"running": "blue", "done": "green", "stopped": "red"}
ROW_STATES = {
    "pending": "대기",
    "running": "처리 중",
    "done": "완료",
    "error": "오류",
    "no_source": "소스 없음",
}
ROW_BADGES = {"pending": "gray", "running": "blue", "error": "red", "no_source": "gray"}
DECISIONS = {"fix": "수정", "no_fix": "수정 불필요"}

STYLE = """
:root {
  --bg: #f4f6f9; --card: #fff; --line: #dfe3ea; --text: #1d2330; --muted: #5d6675;
  --brand: #1f5fbf; --brand-dark: #174a96; --red: #c62828; --red-bg: #fdecec;
  --orange: #b45309; --orange-bg: #fff3e0; --green: #1b7f3b; --green-bg: #e6f4ea;
  --gray-bg: #eef0f3; --add: #e6f6ea; --del: #fdecee;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 "Malgun Gothic", "맑은 고딕", "Segoe UI", sans-serif; }
a { color: var(--brand); }
code { font-family: Consolas, "D2Coding", monospace; }
.topbar { background: #1d2330; color: #fff; }
.topbar .inner { max-width: 1100px; margin: 0 auto; padding: 12px 24px; display: flex;
  gap: 24px; align-items: center; }
.topbar .name { font-weight: 700; font-size: 18px; margin-right: auto; color: #fff;
  text-decoration: none; }
.topbar a { color: #cfd8e8; text-decoration: none; font-size: 15px; }
.topbar a:hover { color: #fff; }
main { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h1 .badge { font-size: 15px; vertical-align: middle; }
h2 { font-size: 19px; margin: 32px 0 12px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 20px 24px; margin-bottom: 16px; }
.card.table { padding: 4px 12px; }
.step { display: flex; gap: 16px; align-items: flex-start; padding: 16px 0;
  border-bottom: 1px solid var(--line); }
.step:last-child { border-bottom: 0; }
.num { flex: none; width: 32px; height: 32px; border-radius: 50%; background: var(--brand);
  color: #fff; display: grid; place-items: center; font-weight: 700; }
.step > div + div { flex: 1; }
.step h3 { margin: 0 0 4px; font-size: 17px; }
.hint { color: var(--muted); font-size: 14px; margin: 0 0 8px; }
input[type=file] { font-size: 15px; padding: 10px; border: 2px dashed #b9c3d3;
  border-radius: 8px; width: 100%; background: #fafbfd; }
.btn { display: inline-block; font: inherit; font-weight: 700; border: 0; border-radius: 8px;
  padding: 12px 22px; cursor: pointer; text-decoration: none; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover { background: var(--brand-dark); }
.btn.primary:disabled { background: #8a94a6; cursor: not-allowed; }
.btn.ghost { background: transparent; color: #fff; border: 1px solid #56607a; font-weight: 400; }
.btn.plain { background: #fff; color: var(--text); border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }
tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-weight: 400; font-size: 14px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 14px;
  font-weight: 700; white-space: nowrap; }
.badge.red { color: var(--red); background: var(--red-bg); }
.badge.orange { color: var(--orange); background: var(--orange-bg); }
.badge.green { color: var(--green); background: var(--green-bg); }
.badge.gray { color: var(--muted); background: var(--gray-bg); }
.badge.blue { color: var(--brand); background: #e8f0fc; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px;
  margin: 16px 0 8px; }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 16px; }
.stat .v { font-size: 26px; font-weight: 700; line-height: 1.2; }
.stat .k { color: var(--muted); font-size: 14px; }
.stat.red .v { color: var(--red); } .stat.orange .v { color: var(--orange); }
.stat.green .v { color: var(--green); }
.progress { height: 10px; background: var(--gray-bg); border-radius: 999px; overflow: hidden;
  margin: 8px 0 4px; }
.progress div { height: 100%; background: var(--brand); }
.notice { background: var(--red-bg); color: var(--red); border-radius: 8px; padding: 12px 16px;
  margin: 12px 0; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin: 20px 0 12px; }
.filters button { font: inherit; font-size: 15px; border: 1px solid var(--line); background: #fff;
  border-radius: 999px; padding: 6px 14px; cursor: pointer; }
.filters button.on { background: var(--text); color: #fff; border-color: var(--text); }
.row { background: var(--card); border: 1px solid var(--line); border-left: 6px solid var(--line);
  border-radius: 10px; margin-bottom: 14px; }
.row.Red { border-left-color: var(--red); } .row.Orange { border-left-color: #f59e0b; }
.row .head { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center;
  padding: 14px 18px; }
.row .check { font-weight: 700; font-size: 17px; }
.row .where { color: var(--muted); font-family: Consolas, "D2Coding", monospace; font-size: 15px; }
.row .right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.pick { display: flex; gap: 6px; align-items: center; font-weight: 700;
  border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
.pick input { width: 18px; height: 18px; }
.row .body { padding: 0 18px 16px; }
.detail { color: var(--muted); margin: 0 0 10px; }
.reason { background: #f3f7fe; border-left: 4px solid var(--brand); padding: 10px 14px;
  border-radius: 6px; margin: 0 0 12px; }
.reason b { display: block; font-size: 13px; color: var(--brand); }
.error { background: var(--red-bg); color: var(--red); padding: 10px 14px; border-radius: 6px; }
pre.diff { margin: 0; background: #fbfcfe; border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 0; overflow-x: auto; font: 15px/1.55 Consolas, "D2Coding", monospace; }
pre.diff span { display: block; padding: 0 14px; white-space: pre; }
pre.diff .add { background: var(--add); } pre.diff .del { background: var(--del); }
pre.diff .hunk { color: #7a8494; }
.howto { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 20px; margin: 16px 0; }
.howto ol { margin: 8px 0 0; padding-left: 22px; }
.cmd { display: flex; gap: 8px; align-items: center; margin: 6px 0; }
.cmd code { flex: 1; background: #1d2330; color: #e6edf7; padding: 8px 12px; border-radius: 6px;
  font-size: 15px; overflow-x: auto; white-space: nowrap; }
.cmd button { font: inherit; font-size: 14px; border: 1px solid var(--line); background: #fff;
  border-radius: 6px; padding: 6px 10px; cursor: pointer; }
.folder { background: var(--gray-bg); padding: 2px 6px; border-radius: 4px; }
.applybar { position: sticky; bottom: 0; background: #1d2330; color: #fff;
  border-radius: 10px 10px 0 0; padding: 14px 20px; display: flex; gap: 16px; align-items: center;
  flex-wrap: wrap; margin-top: 20px; }
.applybar .count { font-weight: 700; margin-right: auto; }
.empty { color: var(--muted); padding: 16px 12px; }
"""

# 걸러 보기, 수정 모두 선택, 선택 개수, 명령 복사. 외부 라이브러리 없이 이 페이지 안에서만 돈다.
SCRIPT = """
const boxes = [...document.querySelectorAll('input[name=row]')];
const count = document.getElementById('count');
const toggle = document.getElementById('toggle-all');
const submit = document.getElementById('download');
function refresh() {
  const n = boxes.filter(b => b.checked).length;
  if (count) count.textContent = `적용할 수정 ${n}개 선택됨 (수정 ${boxes.length}개 중)`;
  if (toggle) toggle.textContent = n && n === boxes.length ? '모두 해제' : '수정 모두 선택';
  if (submit) submit.disabled = n === 0;
}
boxes.forEach(b => b.addEventListener('change', refresh));
if (toggle) toggle.addEventListener('click', () => {
  const all = boxes.every(b => b.checked);
  boxes.forEach(b => { b.checked = !all; });
  refresh();
});
const filters = [...document.querySelectorAll('[data-filter]')];
filters.forEach(button => button.addEventListener('click', () => {
  filters.forEach(b => b.classList.toggle('on', b === button));
  const kind = button.dataset.filter;
  document.querySelectorAll('.row[data-kind]').forEach(row => {
    row.hidden = kind !== 'all' && row.dataset.kind !== kind;
  });
}));
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => {
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    button.textContent = '복사됨';
    setTimeout(() => { button.textContent = '복사'; }, 1500);
  });
}));
refresh();
"""


def page(title: str, body: str, *, refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">{meta}'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · {APP_NAME}</title><style>{STYLE}</style></head><body>"
        f'<div class="topbar"><div class="inner"><a class="name" href="/">{APP_NAME}</a>'
        '<a href="/">새 분석</a><a href="/results">작업 목록</a></div></div>'
        f"<main>{body}</main><script>{SCRIPT}</script></body></html>"
    )


def error_page(title: str, message: str) -> str:
    body = (
        f"<h1>{escape(title)}</h1>"
        f'<div class="card"><div class="error">{escape(message)}</div>'
        '<p><a href="/">새 분석으로 돌아가기</a></p></div>'
    )
    return page(title, body)


def _badge(color: str, text: str) -> str:
    return f'<span class="badge {color}">{escape(text)}</span>'


def _progress(rows: list[Row]) -> tuple[int, int]:
    """(끝난 행 수, 전체 행 수)."""
    done = sum(row.state not in ("pending", "running") for row in rows)
    return done, len(rows)


def _job_badge(job: Job, rows: list[Row]) -> str:
    label = JOB_STATES.get(job.state, job.state)
    if job.state == "running":
        done, total = _progress(rows)
        label = f"{label} {done}/{total}"
    return _badge(JOB_BADGES.get(job.state, "gray"), label)


def _jobs_table(items: list[tuple[Job, list[Row]]]) -> str:
    if not items:
        return '<div class="card table"><p class="empty">아직 작업이 없습니다.</p></div>'
    lines = []
    for job, rows in items:
        colors = Counter(row.finding.color for row in rows)
        when = job.created_at[5:16].replace("T", " ")
        lines.append(
            f"<tr><td>{job.id}</td><td>{escape(when)}</td><td>{escape(job.excel_name)}</td>"
            f"<td>{_badge('red', str(colors['Red']))} {_badge('orange', str(colors['Orange']))}"
            f"</td><td>{_job_badge(job, rows)}</td>"
            f'<td><a href="/results/{job.id}">결과 보기 →</a></td></tr>'
        )
    return (
        '<div class="card table"><table><tr><th>번호</th><th>시각</th><th>엑셀</th>'
        "<th>Red / Orange</th><th>상태</th><th></th></tr>" + "".join(lines) + "</table></div>"
    )


def index_page(recent: list[tuple[Job, list[Row]]]) -> str:
    form = (
        '<form class="card" method="post" action="/results" enctype="multipart/form-data">'
        '<div class="step"><div class="num">1</div><div><h3>Polyspace 결과 엑셀</h3>'
        '<p class="hint">이름이 _Result로 끝나는 RTE 시트가 있는 .xlsx 파일 하나</p>'
        '<input type="file" name="excel" accept=".xlsx" required></div></div>'
        '<div class="step"><div class="num">2</div><div><h3>소스 파일</h3>'
        '<p class="hint">엑셀 File 칸에 나온 .c/.h 파일들. 여러 개를 한 번에 고를 수 있습니다'
        " (Ctrl+클릭)</p>"
        '<input type="file" name="sources" multiple required></div></div>'
        '<div class="step"><div class="num">3</div><div><h3>분석 시작</h3>'
        '<p class="hint">결과 화면으로 넘어가고, 행이 하나씩 처리되는 모습을 볼 수 있습니다</p>'
        '<button class="btn primary" type="submit">분석 시작</button></div></div></form>'
    )
    body = (
        "<h1>새 분석</h1>"
        '<p class="sub">Polyspace 결과 엑셀과 소스 파일을 올리면 Red·Orange 행마다 '
        "LLM이 수정안을 만듭니다.</p>"
        + form
        + '<h2>최근 작업 <a href="/results" style="font-size:15px;font-weight:400">'
        "전체 보기</a></h2>" + _jobs_table(recent)
    )
    return page("새 분석", body)


def results_page(items: list[tuple[Job, list[Row]]]) -> str:
    body = '<h1>작업 목록</h1><p class="sub">최근 작업부터 보여줍니다.</p>' + _jobs_table(items)
    return page("작업 목록", body)


def _diff(diff: str) -> str:
    lines = []
    for number, line in enumerate(diff.split("\n")):
        kind = ""
        if number < 2:
            kind = "hunk"  # --- a/…, +++ b/… 헤더
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        css = f' class="{kind}"' if kind else ""
        lines.append(f"<span{css}>{escape(line)}</span>")
    return '<pre class="diff">' + "".join(lines) + "</pre>"


def _kind(row: Row) -> str:
    """걸러 보기 분류: fix / nofix / problem / waiting."""
    if row.result is not None:
        return "fix" if row.result.decision == "fix" else "nofix"
    return "problem" if row.state in ("error", "no_source") else "waiting"


def _row(row: Row, selectable: bool) -> str:
    finding = row.finding
    where = f"{base_name(finding.file)}:{finding.line if finding.line is not None else '?'}"
    if finding.function:
        where += f" · {finding.function}()"
    if row.result is not None:
        decision = row.result.decision
        status = _badge("blue" if decision == "fix" else "gray", DECISIONS.get(decision, decision))
    else:
        status = _badge(ROW_BADGES.get(row.state, "gray"), ROW_STATES.get(row.state, row.state))
    pick = ""
    if selectable and row.result is not None and row.result.decision == "fix":
        pick = (
            f'<label class="pick"><input type="checkbox" name="row" value="{row.id}"> 적용</label>'
        )
    color_badge = _badge("red" if finding.color == "Red" else "orange", finding.color)
    head = (
        f'<div class="head">{color_badge}<span class="check">{escape(finding.check)}</span>'
        f'<span class="where">{escape(where)}</span>'
        f'<div class="right">{status}{pick}</div></div>'
    )
    body = [f'<p class="detail">엑셀 {finding.row}행 · {escape(finding.detail)}</p>']
    if row.result is not None:
        body.append(f'<div class="reason"><b>LLM 판단 근거</b>{escape(row.result.reason)}</div>')
        if row.result.diff:
            body.append(_diff(row.result.diff))
    if row.error:
        body.append(f'<div class="error">{escape(row.error)}</div>')
    return (
        f'<div class="row {finding.color}" data-kind="{_kind(row)}">'
        f'{head}<div class="body">{"".join(body)}</div></div>'
    )


def _stats(job: Job, rows: list[Row]) -> str:
    colors = Counter(row.finding.color for row in rows)
    kinds = Counter(_kind(row) for row in rows)
    tiles = [
        ("red", colors["Red"], "Red"),
        ("orange", colors["Orange"], "Orange"),
        ("", job.skipped, "제외 (Gray 등)"),
        ("green", kinds["fix"], "수정"),
        ("", kinds["nofix"], "수정 불필요"),
        ("red", kinds["problem"], "오류·소스 없음"),
    ]
    html = [
        f'<div class="stat {color}"><div class="v">{value}</div><div class="k">{label}</div></div>'
        for color, value, label in tiles
    ]
    return '<div class="stats">' + "".join(html) + "</div>"


def _filters(rows: list[Row]) -> str:
    kinds = Counter(_kind(row) for row in rows)
    buttons = [("all", f"전체 {len(rows)}")] + [
        (kind, f"{label} {kinds[kind]}")
        for kind, label in [
            ("fix", "수정"),
            ("nofix", "수정 불필요"),
            ("problem", "오류·소스 없음"),
            ("waiting", "대기"),
        ]
        if kinds[kind]
    ]
    html = []
    for kind, label in buttons:
        on = ' class="on"' if kind == "all" else ""
        html.append(f'<button type="button" data-filter="{kind}"{on}>{escape(label)}</button>')
    return '<div class="filters">' + "".join(html) + "</div>"


def _howto(base: str | None) -> str:
    if base is None:
        folder = "소스 파일이 있는 폴더"
    elif base == "":
        folder = "엑셀 File 경로가 시작되는 폴더"
    else:
        folder = base.replace("/", "\\")
    steps = "".join(
        f'<li><div class="cmd"><code>{escape(command)}</code>'
        f'<button type="button" data-copy="{escape(command)}">복사</button></div></li>'
        for command in (f"{APPLY_COMMAND} --check fixes.patch", f"{APPLY_COMMAND} fixes.patch")
    )
    return (
        '<div class="howto"><b>패치 적용 방법</b> — 내려받은 fixes.patch를 아래 폴더에 두고 '
        f'차례로 실행하세요.<ol><li>폴더: <code class="folder">{escape(folder)}</code></li>'
        f"{steps}</ol></div>"
    )


def job_page(job: Job, rows: list[Row], base: str | None) -> str:
    finished = job.state != "running"
    fixes = sum(1 for row in rows if row.result is not None and row.result.decision == "fix")
    when = job.created_at[5:16].replace("T", " ")
    body = (
        f"<h1>작업 {job.id} · {escape(job.excel_name)} {_job_badge(job, rows)}</h1>"
        f'<p class="sub">시트 {escape(job.sheet)} · {escape(when)} 시작</p>'
    )
    if job.state == "running":
        done, total = _progress(rows)
        percent = round(done * 100 / total) if total else 100
        body += (
            f'<div class="progress"><div style="width:{percent}%"></div></div>'
            f'<p class="sub">{done}/{total} 처리 · 5초마다 새로 고칩니다</p>'
        )
    if job.state == "stopped":
        body += f'<div class="notice">작업이 멈췄습니다: {escape(job.message)}</div>'
    if finished and (job.state == "stopped" or any(row.state == "error" for row in rows)):
        body += (
            f'<form method="post" action="/results/{job.id}/retry">'
            '<button class="btn plain" type="submit">오류 행 다시 시도</button></form>'
        )
    body += _stats(job, rows)
    if not rows:
        body += (
            '<div class="card"><p class="empty">고칠 행 없음 (Red/Orange 행이 없습니다)</p></div>'
        )
        return page(f"작업 {job.id} 결과 · {job.excel_name}", body, refresh=not finished)
    cards = "".join(_row(row, finished) for row in rows)
    body += _filters(rows)
    if finished and fixes:
        body += (
            f'<form method="post" action="/results/{job.id}/fixes.patch">{cards}'
            f"{_howto(base)}"
            '<div class="applybar"><span class="count" id="count"></span>'
            '<button class="btn ghost" type="button" id="toggle-all">수정 모두 선택</button>'
            '<button class="btn primary" type="submit" id="download">패치 내려받기</button>'
            "</div></form>"
        )
    else:
        body += cards
    return page(f"작업 {job.id} 결과 · {job.excel_name}", body, refresh=not finished)
