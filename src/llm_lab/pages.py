"""서비스 화면 HTML. 페이지가 몇 개뿐이라 템플릿 엔진 없이 문자열로 만든다.

외부 CSS·스크립트·글꼴을 쓰지 않는다 (브라우저도 바깥으로 요청하지 않게).
"""

from __future__ import annotations

from collections import Counter
from html import escape

from llm_lab.patch import APPLY_COMMAND, find_conflicts, pick_without_conflicts
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
DECISIONS = {"fix": "수정", "no_fix": "수정 불필요"}
# 화면의 분류: 오류와 소스 없음은 사용자가 손봐야 하므로 "확인 필요"로 묶는다
KIND_LABELS = {"fix": "수정", "nofix": "수정 불필요", "problem": "확인 필요", "waiting": "대기"}
KIND_BADGES = {"fix": "blue", "nofix": "gray", "problem": "amber", "waiting": "gray"}
NO_SOURCE_ACTION = "소스 폴더가 맞는지 확인하고, 맞는 폴더로 다시 분석하세요."

STYLE = """
:root {
  --bg: #f4f6f9; --card: #fff; --line: #e2e6ed; --text: #1d2330; --muted: #5d6675;
  --brand: #1f5fbf; --brand-dark: #174a96; --red: #c62828; --red-bg: #fdecec;
  --orange: #b45309; --orange-bg: #fff3e0; --green: #1b7f3b; --green-bg: #e6f4ea;
  --gray-bg: #eef0f3; --amber: #8a5a00; --amber-bg: #fff5d6; --add: #e6f6ea; --del: #fdecee;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 "Malgun Gothic", "맑은 고딕", "Segoe UI", sans-serif; }
a { color: var(--brand); text-decoration: none; }
code, .mono { font-family: Consolas, "D2Coding", monospace; }
.mono { font-size: 13px; }
.topbar { background: #1d2330; color: #fff; }
.topbar .inner { max-width: 1280px; margin: 0 auto; padding: 8px 20px; display: flex;
  gap: 18px; align-items: center; }
.topbar .name { font-weight: 700; margin-right: auto; color: #fff; }
.topbar a { color: #cfd8e8; font-size: 13px; }
.topbar a:hover { color: #fff; }
main { max-width: 1280px; margin: 0 auto; padding: 14px 20px 0; }
h1 { font-size: 18px; margin: 0; display: flex; gap: 8px; align-items: center; }
h2 { font-size: 15px; margin: 18px 0 8px; }
.sub { color: var(--muted); font-size: 12px; margin: 2px 0 10px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px 16px; margin-bottom: 10px; }
.card.table { padding: 0; overflow: hidden; }
.step { display: flex; gap: 12px; align-items: flex-start; padding: 10px 0;
  border-bottom: 1px solid var(--line); }
.step:last-child { border-bottom: 0; }
.step .num { flex: none; width: 24px; height: 24px; border-radius: 50%; background: var(--brand);
  color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 13px; }
.step > div + div { flex: 1; }
.step h3 { margin: 0 0 2px; font-size: 14px; }
.hint { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
input[type=text] { font: 13px Consolas, "D2Coding", monospace; padding: 6px 8px; width: 100%;
  border: 1px solid #b9c3d3; border-radius: 6px; }
input[type=file] { font-size: 13px; padding: 6px; border: 1px dashed #b9c3d3;
  border-radius: 6px; width: 100%; background: #fafbfd; }
.pickrow { display: flex; gap: 6px; }
.pickrow input { flex: 1; }
.pickrow .btn { white-space: nowrap; }
#pick-note { margin: 4px 0 0; }
.btn { display: inline-block; font: inherit; font-size: 13px; font-weight: 700; border: 0;
  border-radius: 6px; padding: 6px 12px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover { background: var(--brand-dark); }
.btn.primary:disabled { background: #8a94a6; cursor: not-allowed; }
.btn.ghost { background: transparent; color: #fff; border: 1px solid #56607a; font-weight: 400; }
.btn.plain { background: #fff; color: var(--text); border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-weight: 400; font-size: 12px; background: #fafbfc; }
td.num, th.num { text-align: right; width: 64px; white-space: nowrap; }
.list td:last-child, .list th { white-space: nowrap; }
.badge { display: inline-block; padding: 0 7px; border-radius: 999px; font-size: 12px;
  font-weight: 700; line-height: 18px; white-space: nowrap; }
.badge.red { color: var(--red); background: var(--red-bg); }
.badge.orange { color: var(--orange); background: var(--orange-bg); }
.badge.green { color: var(--green); background: var(--green-bg); }
.badge.gray { color: var(--muted); background: var(--gray-bg); }
.badge.blue { color: var(--brand); background: #e8f0fc; }
.badge.amber { color: var(--amber); background: var(--amber-bg); }
.chips { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center;
  background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 12px; margin-bottom: 10px; }
.chips b { font-size: 15px; }
.chips .sep { color: var(--line); }
.progress { height: 6px; background: var(--gray-bg); border-radius: 999px; overflow: hidden;
  margin: 6px 0 2px; }
.progress div { height: 100%; background: var(--brand); }
.notice { background: var(--red-bg); color: var(--red); border-radius: 6px; padding: 6px 10px;
  margin: 6px 0; }
.retry { margin: 6px 0 10px; }
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--line); margin: 0 0 8px; }
.tabs button { font: inherit; font-weight: 700; color: var(--muted); background: none;
  border: 0; border-bottom: 2px solid transparent; padding: 6px 14px; cursor: pointer; }
.tabs button.on { color: var(--text); border-bottom-color: var(--text); }
.filters { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 8px; }
.filters button { font: inherit; font-size: 12px; border: 1px solid var(--line);
  background: #fff; border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.filters button.on { background: var(--text); color: #fff; border-color: var(--text); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
@media (max-width: 800px) { .grid2 { grid-template-columns: 1fr; } }
.box { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 4px; }
.box h3 { font-size: 12px; margin: 0 6px 4px; color: var(--muted); }
.list td.summary { color: var(--muted); max-width: 520px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.list tr.problem td.summary { color: var(--amber); }
.later { color: #cfd8e8; font-size: 12px; }
.item { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--line);
  border-radius: 8px; margin-bottom: 8px; }
.item.Red { border-left-color: var(--red); } .item.Orange { border-left-color: #f59e0b; }
.item .head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 10px; }
.item .check { font-weight: 700; }
.item .where { color: var(--muted); }
.item .right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.pick { display: flex; gap: 4px; align-items: center; cursor: pointer; }
.item .body { padding: 0 10px 8px; }
.detail { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
.reason { background: #f3f7fe; border-left: 3px solid var(--brand); padding: 5px 9px;
  border-radius: 4px; margin: 0 0 6px; }
.excluded { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
.problem-box { background: var(--amber-bg); border-left: 3px solid #e0a400; padding: 6px 10px;
  border-radius: 4px; }
.problem-box .what { font-weight: 700; color: var(--amber); }
.problem-box .todo { margin-top: 2px; }
.problem-box details { margin-top: 4px; color: var(--muted); font-size: 12px;
  white-space: pre-wrap; }
.error { background: var(--red-bg); color: var(--red); padding: 6px 10px; border-radius: 6px; }
pre.diff { margin: 0; background: #fbfcfe; border: 1px solid var(--line); border-radius: 6px;
  padding: 4px 0; overflow-x: auto; font: 12.5px/1.45 Consolas, "D2Coding", monospace; }
pre.diff span { display: block; padding: 0 10px; white-space: pre; }
pre.diff .add { background: var(--add); } pre.diff .del { background: var(--del); }
pre.diff .hunk { color: #7a8494; }
.howto { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 12px; margin: 10px 0; font-size: 13px; }
.howto ol { margin: 4px 0 0; padding-left: 20px; }
.cmd { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
.cmd code { flex: 1; background: #1d2330; color: #e6edf7; padding: 4px 8px; border-radius: 4px;
  font-size: 12.5px; overflow-x: auto; white-space: nowrap; }
.cmd button { font: inherit; font-size: 12px; border: 1px solid var(--line); background: #fff;
  border-radius: 4px; padding: 2px 8px; cursor: pointer; }
.folder { background: var(--gray-bg); padding: 1px 5px; border-radius: 4px; }
.applybar { position: sticky; bottom: 0; background: #1d2330; color: #fff;
  border-radius: 8px 8px 0 0; padding: 8px 14px; display: flex; gap: 10px; align-items: center;
  flex-wrap: wrap; margin-top: 10px; font-size: 13px; }
.applybar .count { font-weight: 700; margin-right: auto; }
.empty { color: var(--muted); padding: 10px 12px; margin: 0; }
"""

# 탭, 걸러 보기, 수정 고르기(겹치는 수정은 함께 못 고름), 명령 복사, 폴더 찾기.
# 외부 라이브러리 없이 이 페이지 안에서만 돈다.
SCRIPT = """
const boxes = [...document.querySelectorAll('input[name=row]')];
const mirrors = [...document.querySelectorAll('input[data-mirror]')];
const auto = boxes.filter(b => b.dataset.auto === '1');
const count = document.getElementById('count');
const toggle = document.getElementById('toggle-all');
const submit = document.getElementById('download');
function refresh() {
  const n = boxes.filter(b => b.checked).length;
  if (count) count.textContent = `적용할 수정 ${n}개 선택됨 (수정 ${boxes.length}개 중)`;
  const allPicked = auto.length && auto.every(b => b.checked);
  if (toggle) toggle.textContent = allPicked ? '모두 해제' : '수정 모두 선택';
  if (submit) submit.disabled = n === 0;
  mirrors.forEach(m => {
    const box = boxes.find(b => b.value === m.dataset.mirror);
    if (box) m.checked = box.checked;
  });
}
function choose(box, on) {
  // 같은 줄을 고치는 수정은 함께 고를 수 없으므로 짝을 푼다
  box.checked = on;
  if (on) (box.dataset.conflicts || '').split(' ').filter(Boolean).forEach(id => {
    const other = boxes.find(b => b.value === id);
    if (other) other.checked = false;
  });
}
boxes.forEach(b => b.addEventListener('change', () => { choose(b, b.checked); refresh(); }));
mirrors.forEach(m => m.addEventListener('change', () => {
  const box = boxes.find(b => b.value === m.dataset.mirror);
  if (box) { choose(box, m.checked); refresh(); }
}));
if (toggle) toggle.addEventListener('click', () => {
  const all = auto.length && auto.every(b => b.checked);
  boxes.forEach(b => { b.checked = false; });
  if (!all) auto.forEach(b => { b.checked = true; });
  refresh();
});
const tabs = [...document.querySelectorAll('[data-tab]')];
function showTab(name) {
  tabs.forEach(t => t.classList.toggle('on', t.dataset.tab === name));
  document.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== name; });
}
tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
document.querySelectorAll('[data-show]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  showTab('detail');
  const target = document.getElementById(link.dataset.show);
  if (target) target.scrollIntoView({block: 'start'});
}));
const filters = [...document.querySelectorAll('[data-filter]')];
filters.forEach(button => button.addEventListener('click', () => {
  filters.forEach(b => b.classList.toggle('on', b === button));
  const kind = button.dataset.filter;
  document.querySelectorAll('[data-kind]').forEach(item => {
    item.hidden = kind !== 'all' && item.dataset.kind !== kind;
  });
}));
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => {
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    button.textContent = '복사됨';
    setTimeout(() => { button.textContent = '복사'; }, 1500);
  });
}));
// 폴더 찾기: 서버가 같은 PC에서 윈도우 폴더 선택 창을 띄우고 고른 경로를 돌려준다
const pick = document.getElementById('pick-folder');
if (pick) pick.addEventListener('click', async () => {
  const input = document.querySelector('input[name=source_dir]');
  const note = document.getElementById('pick-note');
  pick.disabled = true;
  note.textContent = '폴더 선택 창을 열었습니다. 안 보이면 작업 표시줄에서 찾아 주세요.';
  try {
    const response = await fetch('/pick-folder', {
      method: 'POST',
      headers: {'X-Folder-Picker': '1'},
      body: new URLSearchParams({initial: input.value}),
    });
    const data = await response.json();
    if (data.path) {
      input.value = data.path;
      note.textContent = '';
    } else {
      note.textContent = data.error || '폴더를 고르지 않았습니다.';
    }
  } catch (error) {
    note.textContent = '폴더 선택 창을 열지 못했습니다. 경로를 직접 붙여넣으세요.';
  } finally {
    pick.disabled = false;
  }
});
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


def index_page(recent: list[tuple[Job, list[Row]]], recent_dirs: list[str]) -> str:
    options = "".join(f'<option value="{escape(folder)}">' for folder in recent_dirs)
    form = (
        '<form class="card" method="post" action="/results" enctype="multipart/form-data">'
        '<div class="step"><div class="num">1</div><div><h3>Polyspace 결과 엑셀</h3>'
        '<p class="hint">이름이 _Result로 끝나는 RTE 시트가 있는 .xlsx 파일 하나</p>'
        '<input type="file" name="excel" accept=".xlsx" required></div></div>'
        '<div class="step"><div class="num">2</div><div><h3>소스 폴더</h3>'
        '<p class="hint">소스 코드가 들어 있는 최상위 폴더. "폴더 찾기…"로 고르거나 경로를 '
        "붙여넣습니다. 엑셀에 나온 파일을 이 폴더 안에서 찾고, 패치도 이 폴더 기준으로 "
        "만듭니다.</p>"
        '<div class="pickrow"><input type="text" name="source_dir" list="recent-dirs" required '
        r'placeholder="예: C:\work\brake\src" autocomplete="off">'
        '<button class="btn plain" type="button" id="pick-folder">폴더 찾기…</button></div>'
        '<p class="hint" id="pick-note"></p>'
        f'<datalist id="recent-dirs">{options}</datalist></div></div>'
        '<div class="step"><div class="num">3</div><div><h3>분석 시작</h3>'
        '<p class="hint">결과 화면으로 넘어가고, 행이 하나씩 처리되는 모습을 볼 수 있습니다</p>'
        '<button class="btn primary" type="submit">분석 시작</button></div></div></form>'
    )
    body = (
        "<h1>새 분석</h1>"
        '<p class="sub">Polyspace 결과 엑셀과 소스 폴더를 주면 Red·Orange 행마다 '
        "LLM이 수정안을 만듭니다.</p>"
        + form
        + '<h2>최근 작업 <a href="/results" style="font-size:12px;font-weight:400">'
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
    """화면 분류: fix / nofix / problem(확인 필요) / waiting."""
    if row.result is not None:
        return "fix" if row.result.decision == "fix" else "nofix"
    return "problem" if row.state in ("error", "no_source") else "waiting"


def _explain(row: Row) -> tuple[str, str, str]:
    """확인 필요 행의 (무엇이 문제인지, 할 일, 개발자용 내용).

    오류는 fixer.user_error() 형식(첫 줄 / "할 일: " / "자세히: ")으로 저장된다.
    """
    lines = row.error.split("\n") if row.error else [ROW_STATES.get(row.state, row.state)]
    summary, action, details = lines[0], "", []
    for line in lines[1:]:
        if line.startswith("할 일: "):
            action = line.removeprefix("할 일: ")
        else:
            details.append(line.removeprefix("자세히: "))
    if row.state == "no_source" and not action:
        action = NO_SOURCE_ACTION
    return summary, action, "\n".join(details)


def _first_sentence(text: str) -> str:
    head, separator, _ = text.partition(". ")
    return head + ("." if separator else "")


def _file_label(job: Job, row: Row) -> str:
    """소스 폴더 기준 경로(폴더에서 찾은 파일) 또는 엑셀의 파일 이름."""
    if row.file_name and job.source_root:
        return row.file_name
    return base_name(row.finding.file)


def _selection(rows: list[Row]) -> tuple[dict[int, set[int]], set[int]]:
    """수정 행끼리의 충돌과, "수정 모두 선택" 때 고를 행 (Red 먼저, 그다음 엑셀 행 순)."""
    by_id = {row.id: row for row in rows}
    fixable = {
        row.id: (row.file_name or "", list(row.result.edits))
        for row in rows
        if row.file_name and row.result is not None and row.result.decision == "fix"
    }
    conflicts = find_conflicts(fixable)
    order = sorted(
        fixable,
        key=lambda row_id: (by_id[row_id].finding.color != "Red", by_id[row_id].finding.row),
    )
    return conflicts, pick_without_conflicts(order, conflicts)


def _partner(row: Row, conflicts: dict[int, set[int]], picked: set[int], by_id: dict[int, Row]):
    """이번에 빠진 수정 행과 같은 줄을 고치는, 골라진 행 하나 (없으면 None)."""
    chosen = sorted(conflicts.get(row.id, set()) & picked, key=lambda i: by_id[i].finding.row)
    return by_id[chosen[0]] if chosen else None


def _chips(job: Job, rows: list[Row]) -> str:
    colors = Counter(row.finding.color for row in rows)
    kinds = Counter(_kind(row) for row in rows)
    parts = [
        f"<span>{_badge('red', 'Red')} <b>{colors['Red']}</b></span>",
        f"<span>{_badge('orange', 'Orange')} <b>{colors['Orange']}</b></span>",
        _badge("gray", f"제외 {job.skipped}"),
        '<span class="sep">|</span>',
    ]
    for kind in ("fix", "nofix", "problem", "waiting"):
        if kind != "waiting" or kinds[kind]:
            label = _badge(KIND_BADGES[kind], KIND_LABELS[kind])
            parts.append(f"<span>{label} <b>{kinds[kind]}</b></span>")
    return '<div class="chips">' + " ".join(parts) + "</div>"


def _group_table(title: str, groups: dict[str, Counter], mono: bool) -> str:
    css = ' class="mono"' if mono else ""
    lines = []
    for name, counts in sorted(groups.items(), key=lambda item: (-item[1]["all"], item[0])):
        cells = "".join(
            f'<td class="num">{counts[key]}</td>' for key in ("all", "fix", "nofix", "problem")
        )
        lines.append(f"<tr><td{css}>{escape(name)}</td>{cells}</tr>")
    return (
        f'<div class="box"><h3>{title}</h3><table><tr><th></th><th class="num">전체</th>'
        '<th class="num">수정</th><th class="num">불필요</th><th class="num">확인 필요</th></tr>'
        + "".join(lines)
        + "</table></div>"
    )


def _groups(job: Job, rows: list[Row]) -> str:
    by_check: dict[str, Counter] = {}
    by_file: dict[str, Counter] = {}
    for row in rows:
        kind = _kind(row)
        for groups, key in ((by_check, row.finding.check), (by_file, _file_label(job, row))):
            counts = groups.setdefault(key, Counter())
            counts["all"] += 1
            counts[kind] += 1
    return (
        '<div class="grid2">'
        + _group_table("검사 종류별", by_check, mono=False)
        + _group_table("파일별", by_file, mono=True)
        + "</div>"
    )


def _filters(rows: list[Row]) -> str:
    kinds = Counter(_kind(row) for row in rows)
    buttons = [("all", f"전체 {len(rows)}")] + [
        (kind, f"{KIND_LABELS[kind]} {kinds[kind]}")
        for kind in ("fix", "nofix", "problem", "waiting")
        if kinds[kind]
    ]
    html = []
    for kind, label in buttons:
        on = ' class="on"' if kind == "all" else ""
        html.append(f'<button type="button" data-filter="{kind}"{on}>{escape(label)}</button>')
    return '<div class="filters">' + "".join(html) + "</div>"


def _status_badge(row: Row) -> str:
    kind = _kind(row)
    if kind == "waiting":
        return _badge("blue" if row.state == "running" else "gray", ROW_STATES[row.state])
    return _badge(KIND_BADGES[kind], KIND_LABELS[kind])


def _overview_row(
    job: Job,
    row: Row,
    finished: bool,
    conflicts: dict[int, set[int]],
    picked: set[int],
    by_id: dict[int, Row],
) -> str:
    finding = row.finding
    kind = _kind(row)
    box = ""
    if finished and row.id in conflicts:
        others = " ".join(str(other) for other in sorted(conflicts[row.id]))
        auto = 1 if row.id in picked else 0
        box = (
            f'<input type="checkbox" name="row" value="{row.id}" data-auto="{auto}" '
            f'data-conflicts="{others}">'
        )
    if kind == "problem":
        summary, action, _ = _explain(row)
        text = summary + (f" → {_first_sentence(action)}" if action else "")
        summary_html, title = escape(text), text
    elif row.result is not None:
        partner = _partner(row, conflicts, picked, by_id) if row.id not in picked else None
        title = row.result.reason
        summary_html = escape(row.result.reason)
        if kind == "fix" and partner is not None:
            summary_html = (
                f"{_badge('gray', '이번엔 제외')} 행 {partner.finding.row} 수정과 같은 줄을 "
                f"고칩니다 · {summary_html}"
            )
    else:
        title = ROW_STATES.get(row.state, row.state)
        summary_html = escape(title)
    line = finding.line if finding.line is not None else "?"
    color = _badge("red" if finding.color == "Red" else "orange", finding.color)
    css = ' class="problem"' if kind == "problem" else ""
    return (
        f'<tr data-kind="{kind}"{css}><td>{box}</td><td>{finding.row}</td><td>{color}</td>'
        f"<td>{escape(finding.check)}</td>"
        f'<td class="mono">{escape(_file_label(job, row))}:{line}</td>'
        f"<td>{_status_badge(row)}</td>"
        f'<td class="summary" title="{escape(title)}">{summary_html}</td>'
        f'<td><a href="#row-{row.id}" data-show="row-{row.id}">보기</a></td></tr>'
    )


def _overview(job: Job, rows: list[Row], finished: bool, conflicts, picked) -> str:
    by_id = {row.id: row for row in rows}
    lines = "".join(_overview_row(job, row, finished, conflicts, picked, by_id) for row in rows)
    return (
        _groups(job, rows) + '<div class="card table list"><table><tr><th style="width:26px"></th>'
        '<th style="width:44px">행</th><th style="width:64px">종류</th><th>검사</th>'
        '<th>위치</th><th style="width:90px">판단</th><th>요약</th>'
        '<th style="width:36px"></th></tr>' + lines + "</table></div>"
    )


def _detail_card(
    job: Job,
    row: Row,
    finished: bool,
    conflicts: dict[int, set[int]],
    picked: set[int],
    by_id: dict[int, Row],
) -> str:
    finding = row.finding
    where = f"{_file_label(job, row)}:{finding.line if finding.line is not None else '?'}"
    if finding.function:
        where += f" · {finding.function}()"
    mirror = ""
    if finished and row.id in conflicts:
        mirror = f'<label class="pick"><input type="checkbox" data-mirror="{row.id}"> 적용</label>'
    color = _badge("red" if finding.color == "Red" else "orange", finding.color)
    head = (
        f'<div class="head">{color}<span class="check">{escape(finding.check)}</span>'
        f'<span class="where mono">{escape(where)}</span>'
        f'<div class="right">{_status_badge(row)}{mirror}</div></div>'
    )
    body = [f'<p class="detail">엑셀 {finding.row}행 · {escape(finding.detail)}</p>']
    if row.result is not None:
        partner = _partner(row, conflicts, picked, by_id) if row.id not in picked else None
        if row.result.decision == "fix" and partner is not None:
            body.append(
                f'<p class="excluded">{_badge("gray", "이번엔 제외")} 행 {partner.finding.row} '
                "수정과 같은 줄을 고칩니다. 이번 패치를 적용하고 Polyspace를 다시 돌린 뒤 "
                "다시 분석하면 남은 문제만 다시 고칩니다.</p>"
            )
        body.append(f'<div class="reason">{escape(row.result.reason)}</div>')
        if row.result.diff:
            body.append(_diff(row.result.diff))
    elif _kind(row) == "problem":
        summary, action, detail = _explain(row)
        more = ""
        if row.state == "error":
            log = f"대화 기록: private/llm-logs/ 안의 …_job{job.id}_row{finding.row}.md"
            text = "\n".join(part for part in (detail, log) if part)
            more = f"<details><summary>자세히 (개발자용)</summary>{escape(text)}</details>"
        todo = f'<div class="todo">할 일: {escape(action)}</div>' if action else ""
        body.append(
            f'<div class="problem-box"><div class="what">{escape(summary)}</div>{todo}{more}</div>'
        )
    return (
        f'<div class="item {finding.color}" id="row-{row.id}" data-kind="{_kind(row)}">'
        f'{head}<div class="body">{"".join(body)}</div></div>'
    )


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
    when = job.created_at[5:16].replace("T", " ")
    body = (
        f"<h1>작업 {job.id} · {escape(job.excel_name)} {_job_badge(job, rows)}</h1>"
        f'<p class="sub">시트 {escape(job.sheet)} · {escape(when)} 시작'
        + (f" · 소스 폴더 {escape(job.source_root)}" if job.source_root else "")
        + "</p>"
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
            f'<form class="retry" method="post" action="/results/{job.id}/retry">'
            '<button class="btn plain" type="submit">오류 행 다시 시도</button></form>'
        )
    body += _chips(job, rows)
    title = f"작업 {job.id} 결과 · {job.excel_name}"
    if not rows:
        body += (
            '<div class="card"><p class="empty">고칠 행 없음 (Red/Orange 행이 없습니다)</p></div>'
        )
        return page(title, body, refresh=not finished)
    conflicts, picked = _selection(rows) if finished else ({}, set())
    by_id = {row.id: row for row in rows}
    cards = "".join(_detail_card(job, row, finished, conflicts, picked, by_id) for row in rows)
    panels = (
        '<div class="tabs"><button type="button" data-tab="overview" class="on">한눈에 보기'
        '</button><button type="button" data-tab="detail">자세히 보기 (diff)</button></div>'
        + _filters(rows)
        + f'<div data-panel="overview">{_overview(job, rows, finished, conflicts, picked)}</div>'
        + f'<div data-panel="detail" hidden>{cards}</div>'
    )
    if finished and conflicts:
        later = len(conflicts) - len(picked)
        note = f'<span class="later">같은 줄 겹침으로 {later}개는 다음 차례</span>' if later else ""
        body += (
            f'<form method="post" action="/results/{job.id}/fixes.patch">{panels}'
            f"{_howto(base)}"
            '<div class="applybar"><span class="count" id="count"></span>'
            f"{note}"
            '<button class="btn ghost" type="button" id="toggle-all">수정 모두 선택</button>'
            '<button class="btn primary" type="submit" id="download">패치 내려받기</button>'
            "</div></form>"
        )
    else:
        body += panels
    return page(title, body, refresh=not finished)
