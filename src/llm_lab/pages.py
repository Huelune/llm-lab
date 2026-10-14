"""서비스 화면 HTML. 페이지가 몇 개뿐이라 템플릿 엔진 없이 문자열로 만든다.

외부 CSS·스크립트·글꼴을 쓰지 않는다 (브라우저도 바깥으로 요청하지 않게).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from html import escape
from urllib.parse import quote

from llm_lab.kinds import KINDS, Kind
from llm_lab.patch import APPLY_COMMAND, find_conflicts, pick_without_conflicts
from llm_lab.safe_text import safe
from llm_lab.sources import base_name
from llm_lab.store import USER_STOP, Job, Row

APP_NAME = "Polyspace 수정 제안"
JOB_STATES = {"running": "처리 중", "done": "완료", "stopped": "멈춤"}
JOB_BADGES = {"running": "blue", "done": "green", "stopped": "red"}
ROW_STATES = {
    "pending": "대기",
    "running": "처리 중",
    "done": "완료",
    "error": "오류",
    "no_source": "소스 없음",
}
DECISIONS = {"fix": "수정", "no_fix": "수정 불필요", "manual": "직접 수정"}
# 화면의 분류. "직접 수정"은 LLM이 고쳐야 한다고 봤지만 사람이 정해야 하는 행(diff 없음),
# "확인 필요"는 오류와 소스 없음처럼 사용자가 손봐야 하는 행이다
KIND_LABELS = {
    "fix": "수정",
    "nofix": "수정 불필요",
    "manual": "직접 수정",
    "problem": "확인 필요",
    "waiting": "대기",
}
KIND_BADGES = {
    "fix": "blue",
    "nofix": "gray",
    "manual": "orange",
    "problem": "amber",
    "waiting": "gray",
}
KIND_ORDER = ("fix", "nofix", "manual", "problem", "waiting")
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
.spinner { display: inline-block; width: 14px; height: 14px; margin-right: 6px;
  vertical-align: -2px; border: 2px solid var(--gray-bg); border-top-color: var(--brand);
  border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
.notice { background: var(--red-bg); color: var(--red); border-radius: 6px; padding: 6px 10px;
  margin: 6px 0; }
.notice.paused { background: var(--gray-bg); color: var(--text); }
.kind { margin: 2px 0; }
.modes { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 6px 0 2px; }
.choices { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 2px 0 6px 22px;
  font-size: 13px; }
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
/* 검사 종류가 많아도(MISRA 규칙 100여 개) 상자 안에서 스크롤하고, 이름은 한 줄로 줄인다 */
.box { max-height: 340px; overflow: auto; }
.box table { table-layout: fixed; }
.box th { position: sticky; top: 0; }
.box td:first-child, .box th:first-child { overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.box td.num, .box th.num { width: 40px; padding-left: 4px; padding-right: 4px; }
.box th.num { white-space: normal; word-break: keep-all; line-height: 1.2; vertical-align: bottom; }
/* 요약(캡처용)은 스크롤 없이 한 장에 다 보이고 머리글이 한 줄이다 */
.summary-page .box { max-height: none; }
.summary-page .box td.num, .summary-page .box th.num { width: 76px; white-space: nowrap; }
.box h3 { font-size: 12px; margin: 0 6px 4px; color: var(--muted); }
/* 칸 폭을 고정해 긴 요약·검사·위치는 줄이고, 맨 끝 "보기"가 카드 밖으로 밀리지 않게 한다 */
.list table { table-layout: fixed; }
.list td.summary, .list td.mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.list td.summary { color: var(--muted); }
.list tr.problem td.summary { color: var(--amber); }
.list td.check { overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.later { color: #cfd8e8; font-size: 12px; }
.item { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--line);
  border-radius: 8px; margin-bottom: 8px; }
.item.band-red { border-left-color: var(--red); }
.item.band-orange { border-left-color: #f59e0b; }
.item.band-gray { border-left-color: #b9c3d3; }
.item .head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 10px; }
.item .check { font-weight: 700; }
.item .where { color: var(--muted); }
.item .right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.pick { display: flex; gap: 4px; align-items: center; cursor: pointer; }
.item .body { padding: 0 10px 8px; }
.detail { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
.item.hit { outline: 2px solid var(--brand); outline-offset: 2px; }  /* "보기"로 찾아온 카드 */
.reason { background: #f3f7fe; border-left: 3px solid var(--brand); padding: 5px 9px;
  border-radius: 4px; margin: 0 0 6px; }
.excluded { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
.note { color: var(--amber); font-size: 12px; margin: 0 0 6px; }
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
  history.replaceState(null, '', '#' + name);  // 새로 고침 뒤에도 보던 탭이 열린다
}
tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
function highlight(card) {
  document.querySelectorAll('.item.hit').forEach(item => item.classList.remove('hit'));
  card.classList.add('hit');
}
document.querySelectorAll('[data-show]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  showTab('detail');
  const target = document.getElementById(link.dataset.show);
  if (!target) return;
  highlight(target);
  history.replaceState(null, '', '#' + target.id);  // 다시 읽어도 같은 카드를 강조한다
  target.scrollIntoView({block: 'start'});
}));
// 처음 열 때: 주소의 #이 탭이면 그 탭을, 카드면 자세히 보기 탭에서 그 카드를 강조한다
const wanted = location.hash.slice(1);
const wantedCard = wanted ? document.getElementById(wanted) : null;
if (tabs.some(t => t.dataset.tab === wanted)) {
  showTab(wanted);
} else if (wantedCard && wantedCard.classList.contains('item')) {
  showTab('detail');
  history.replaceState(null, '', '#' + wanted);
  highlight(wantedCard);
}
// 다시 읽어도 화면이 그대로 남도록 이 탭의 sessionStorage에 페이지별로 적어 둔다
function recall(name) {
  try { return sessionStorage.getItem(name + ':' + location.pathname); } catch (e) { return null; }
}
function remember(name, value) {
  try { sessionStorage.setItem(name + ':' + location.pathname, value); } catch (e) {}
}
function forget(name) {
  try { sessionStorage.removeItem(name + ':' + location.pathname); } catch (e) {}
}
// 걸러 보기: 고른 종류를 기억했다가 다시 읽은 뒤에도 그 종류만 보인다
const filters = [...document.querySelectorAll('[data-filter]')];
function applyFilter(kind) {
  filters.forEach(b => b.classList.toggle('on', b.dataset.filter === kind));
  document.querySelectorAll('[data-kind]').forEach(item => {
    item.hidden = kind !== 'all' && item.dataset.kind !== kind;
  });
}
filters.forEach(button => button.addEventListener('click', () => {
  applyFilter(button.dataset.filter);
  remember('filter', button.dataset.filter);
}));
const savedFilter = recall('filter');
if (savedFilter && filters.some(b => b.dataset.filter === savedFilter)) applyFilter(savedFilter);
// 펼쳐 둔 '자세히'는 다시 읽은 뒤에도 펼친다
const folds = [...document.querySelectorAll('details')];
(recall('open') || '').split(' ').filter(Boolean).forEach(i => {
  if (folds[Number(i)]) folds[Number(i)].open = true;
});
forget('open');
// 스크롤: 다시 읽기 전에 저장해 둔 위치가 있으면 그리로, 없고 카드가 있으면 카드로
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
const scrollBack = recall('scroll');
if (scrollBack !== null) {
  forget('scroll');
  window.scrollTo(0, Number(scrollBack));
} else if (wantedCard) {
  wantedCard.scrollIntoView({block: 'start'});
}
// 분석 시작을 누르면 엑셀을 읽는 몇 초 동안 단추를 잠그고 스피너를 보인다
const analyze = document.getElementById('analyze');
if (analyze) analyze.addEventListener('submit', () => {
  const button = analyze.querySelector('button[type="submit"]');
  if (!button) return;
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>엑셀을 읽는 중…';
});
// 처리 중인 작업 페이지는 정해진 간격마다 새 내용을 받아 보고, 바뀌었을 때만 다시 읽는다.
// 다시 읽을 때는 주소의 #(탭·카드), 걸러 보기, 펼친 '자세히', 스크롤이 남는다
const live = document.querySelector('main[data-refresh]');
async function poll() {
  try {
    const response = await fetch(location.pathname, {cache: 'no-store'});
    const found = (await response.text()).match(/data-version="(\\w+)"/);
    if (response.ok && (!found || found[1] !== live.dataset.version)) {
      remember('scroll', String(window.scrollY));
      remember('open', folds.map((f, i) => f.open ? i : '').filter(String).join(' '));
      location.reload();
      return;
    }
  } catch (e) {}
  setTimeout(poll, Number(live.dataset.refresh) * 1000);
}
if (live) setTimeout(poll, Number(live.dataset.refresh) * 1000);
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => {
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    button.textContent = '복사됨';
    setTimeout(() => { button.textContent = '복사'; }, 1500);
  });
}));
// 새 분석: 고른 종류의 대상 체크 상자만 보인다
const choiceBoxes = [...document.querySelectorAll('[data-choices]')];
function showChoices(name) {
  choiceBoxes.forEach(box => { box.hidden = box.dataset.choices !== name; });
}
document.querySelectorAll('input[name=kind]').forEach(radio => {
  radio.addEventListener('change', () => showChoices(radio.value));
});
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


# 탭 아이콘: 파란 둥근 네모 위의 체크 표시. 외부 파일을 읽지 않도록 data URI로 넣고
# /favicon.ico로도 같은 그림을 준다
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1f5fbf"/>'
    '<path d="M8 17l5 5 11-12" fill="none" stroke="#fff" stroke-width="3.5" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
FAVICON_HREF = "data:image/svg+xml," + quote(FAVICON_SVG)


def page(title: str, body: str, *, refresh: bool = False) -> str:
    """refresh면 스크립트가 5초마다 내용 지문(data-version)을 확인해 바뀌었을 때만 다시 읽는다."""
    main = "<main>"
    if refresh:
        version = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        main = f'<main data-refresh="5" data-version="{version}">'
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="icon" href="{FAVICON_HREF}">'
        f"<title>{escape(title)} · {APP_NAME}</title><style>{STYLE}</style></head><body>"
        f'<div class="topbar"><div class="inner"><a class="name" href="/">{APP_NAME}</a>'
        '<a href="/">새 분석</a><a href="/results">작업 목록</a></div></div>'
        f"{main}{body}</main><script>{SCRIPT}</script></body></html>"
    )


def error_page(title: str, message: str) -> str:
    body = (
        f"<h1>{escape(title)}</h1>"
        f'<div class="card"><div class="error">{escape(message)}</div>'
        '<p><a href="/">새 분석으로 돌아가기</a></p></div>'
    )
    return page(title, body)


def _badge(color: str, text: str, title: str = "") -> str:
    tip = f' title="{escape(title)}"' if title else ""
    return f'<span class="badge {color}"{tip}>{escape(text)}</span>'


def _label_counts(kind: Kind, rows: list[Row]) -> list[tuple[str, int]]:
    """배지 글자(대상)별 행 수. 중요한 대상부터."""
    counts = Counter(kind.label(row.finding) for row in rows)
    return sorted(counts.items(), key=lambda item: (kind.rank(item[0]), item[0]))


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
        kind = KINDS[job.kind]
        labels = " ".join(
            _badge(kind.color(label), f"{label} {count}")
            for label, count in _label_counts(kind, rows)
        )
        when = job.created_at[5:16].replace("T", " ")
        lines.append(
            f"<tr><td>{job.id}</td><td>{escape(when)}</td><td>{escape(job.excel_name)}</td>"
            f"<td>{escape(kind.title)} {labels}</td><td>{_job_badge(job, rows)}</td>"
            f'<td><a href="/results/{job.id}">결과 보기 →</a></td></tr>'
        )
    return (
        '<div class="card table"><table><tr><th>번호</th><th>시각</th><th>엑셀</th>'
        "<th>종류 · 대상</th><th>상태</th><th></th></tr>" + "".join(lines) + "</table></div>"
    )


def _kind_step() -> str:
    """분석할 시트(종류) 라디오와 종류별 대상 체크 상자. 처음에는 첫 종류(RTE)를 고른다."""
    blocks = []
    for number, kind in enumerate(KINDS.values()):
        first = number == 0
        boxes = "".join(
            f'<label><input type="checkbox" name="choice" '
            f'value="{escape(kind.name)}:{escape(label)}"'
            f"{' checked' if label in kind.defaults else ''}> {escape(label)}</label>"
            for label in kind.choices
        )
        if kind.modes:
            radios = "".join(
                f'<label><input type="radio" name="mode" '
                f'value="{escape(kind.name)}:{escape(value)}"'
                f"{' checked' if number_ == 0 else ''}> {escape(label)}</label>"
                for number_, (value, label, _) in enumerate(kind.modes)
            )
            helps = " / ".join(f"{escape(label)}: {escape(help)}" for _, label, help in kind.modes)
            boxes += f'<div class="modes">처리 방식: {radios}</div><p class="hint">{helps}</p>'
        blocks.append(
            f'<div class="kind"><label><input type="radio" name="kind" '
            f'value="{escape(kind.name)}"{" checked" if first else ""}> '
            f"<b>{escape(kind.title)}</b></label>"
            f'<div class="choices" data-choices="{escape(kind.name)}"{"" if first else " hidden"}>'
            f"{boxes}</div></div>"
        )
    return (
        '<div class="step"><div class="num">3</div><div><h3>분석할 시트</h3>'
        '<p class="hint">고른 종류의 결과 시트를 읽고, 체크한 대상만 LLM에 보냅니다.</p>'
        + "".join(blocks)
        + "</div></div>"
    )


def index_page(recent: list[tuple[Job, list[Row]]], recent_dirs: list[str]) -> str:
    options = "".join(f'<option value="{escape(folder)}">' for folder in recent_dirs)
    form = (
        '<form id="analyze" class="card" method="post" action="/results" '
        'enctype="multipart/form-data">'
        '<div class="step"><div class="num">1</div><div><h3>Polyspace 결과 엑셀</h3>'
        '<p class="hint">이름이 _Result로 끝나는 결과 시트가 있는 .xlsx 파일 하나</p>'
        '<input type="file" name="excel" accept=".xlsx" required></div></div>'
        '<div class="step"><div class="num">2</div><div><h3>소스 폴더 (프로젝트 전체)</h3>'
        '<p class="hint">부르는 함수와 헤더까지 모두 들어 있는 프로젝트 최상위 폴더. '
        '"폴더 찾기…"로 고르거나 경로를 붙여넣습니다. 엑셀에 나온 파일을 이 폴더 안에서 '
        "찾고, 패치도 이 폴더 기준으로 만듭니다.</p>"
        '<div class="pickrow"><input type="text" name="source_dir" list="recent-dirs" required '
        r'placeholder="예: C:\work\brake" autocomplete="off">'
        '<button class="btn plain" type="button" id="pick-folder">폴더 찾기…</button></div>'
        '<p class="hint" id="pick-note"></p>'
        f'<datalist id="recent-dirs">{options}</datalist></div></div>'
        + _kind_step()
        + '<div class="step"><div class="num">4</div><div><h3>분석 시작</h3>'
        '<p class="hint">결과 화면으로 넘어가고, 행이 하나씩 처리되는 모습을 볼 수 있습니다</p>'
        '<button class="btn primary" type="submit">분석 시작</button></div></div></form>'
    )
    body = (
        "<h1>새 분석</h1>"
        '<p class="sub">Polyspace 결과 엑셀과 소스 폴더를 주면 고른 시트의 행마다 '
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
    """화면 분류: fix / nofix / manual(직접 수정) / problem(확인 필요) / waiting."""
    if row.result is not None:
        return {"fix": "fix", "manual": "manual"}.get(row.result.decision, "nofix")
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


def _selection(kind: Kind, rows: list[Row]) -> tuple[dict[int, set[int]], set[int], set[int]]:
    """수정 행끼리의 충돌, "수정 모두 선택" 때 고를 행, 종류가 자동으로 고르지 않는 수정 행.

    고르는 순서는 중요한 대상 먼저, 그다음 엑셀 행 순서다.
    """
    by_id = {row.id: row for row in rows}
    fixable = {
        row.id: (row.file_name or "", list(row.result.edits))
        for row in rows
        if row.file_name and row.result is not None and row.result.decision == "fix"
    }
    conflicts = find_conflicts(fixable)
    manual = {
        row_id
        for row_id in fixable
        if not kind.auto_select(by_id[row_id].finding) or _edits_other_files(by_id[row_id])
    }
    order = sorted(
        (row_id for row_id in fixable if row_id not in manual),
        key=lambda row_id: (kind.priority(by_id[row_id].finding), by_id[row_id].finding.row),
    )
    return conflicts, pick_without_conflicts(order, conflicts), manual


def _partner(row: Row, conflicts: dict[int, set[int]], picked: set[int], by_id: dict[int, Row]):
    """이번에 빠진 수정 행과 같은 줄을 고치는, 골라진 행 하나 (없으면 None)."""
    chosen = sorted(conflicts.get(row.id, set()) & picked, key=lambda i: by_id[i].finding.row)
    return by_id[chosen[0]] if chosen else None


def _chips(kind: Kind, job: Job, rows: list[Row]) -> str:
    kinds = Counter(_kind(row) for row in rows)
    # 고를 수 있는 대상은 0개여도 보인다 (패치 뒤 다시 분석하면 Red가 0개일 때가 많다)
    counts = dict(_label_counts(kind, rows))
    labels = [*kind.choices, *(label for label in counts if label not in kind.choices)]
    parts = [
        f"<span>{_badge(kind.color(label), label)} <b>{counts.get(label, 0)}</b></span>"
        for label in labels
    ]
    parts += [_badge("gray", f"제외 {job.skipped}"), '<span class="sep">|</span>']
    for kind in KIND_ORDER:
        if kind != "waiting" or kinds[kind]:
            label = _badge(KIND_BADGES[kind], KIND_LABELS[kind])
            parts.append(f"<span>{label} <b>{kinds[kind]}</b></span>")
    return '<div class="chips">' + " ".join(parts) + "</div>"


def _group_table(title: str, groups: dict[str, Counter], mono: bool) -> str:
    css = ' class="mono"' if mono else ""
    lines = []
    for name, counts in sorted(groups.items(), key=lambda item: (-item[1]["all"], item[0])):
        cells = "".join(
            f'<td class="num">{counts[key]}</td>'
            for key in ("all", "fix", "nofix", "manual", "problem")
        )
        # 긴 이름(MISRA 규칙 문장)은 CSS가 한 줄로 줄이므로 전체는 title로 보인다
        lines.append(f'<tr><td{css} title="{escape(name)}">{escape(name)}</td>{cells}</tr>')
    return (
        f'<div class="box"><h3>{title}</h3><table><tr><th></th><th class="num">전체</th>'
        '<th class="num">수정</th><th class="num">불필요</th><th class="num">직접 수정</th>'
        '<th class="num">확인 필요</th></tr>' + "".join(lines) + "</table></div>"
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
        (kind, f"{KIND_LABELS[kind]} {kinds[kind]}") for kind in KIND_ORDER if kinds[kind]
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
    sheet_kind: Kind,
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
        if row.result.decision == "fix" and _edits_other_files(row):
            summary_html = f"{_badge('blue', '다른 파일 수정')} {summary_html}"
    else:
        title = ROW_STATES.get(row.state, row.state)
        summary_html = escape(title)
    line = finding.line if finding.line is not None else "?"
    label = sheet_kind.label(finding)
    badge = _badge(sheet_kind.color(label), label, finding.note)
    css = ' class="problem"' if kind == "problem" else ""
    return (
        f'<tr data-kind="{kind}"{css}><td>{box}</td><td>{finding.row}</td><td>{badge}</td>'
        f'<td class="check" title="{escape(finding.check)}">{escape(finding.check)}</td>'
        f'<td class="mono">{escape(_file_label(job, row))}:{line}</td>'
        f"<td>{_status_badge(row)}</td>"
        f'<td class="summary" title="{escape(title)}">{summary_html}</td>'
        f'<td><a href="#row-{row.id}" data-show="row-{row.id}">보기</a></td></tr>'
    )


def _overview(kind: Kind, job: Job, rows: list[Row], finished: bool, conflicts, picked) -> str:
    by_id = {row.id: row for row in rows}
    lines = "".join(
        _overview_row(kind, job, row, finished, conflicts, picked, by_id) for row in rows
    )
    return (
        _groups(job, rows) + '<div class="card table list"><table><tr><th style="width:26px"></th>'
        '<th style="width:44px">행</th><th style="width:64px">종류</th>'
        '<th style="width:22%">검사</th><th style="width:18%">위치</th>'
        '<th style="width:90px">판단</th><th>요약</th><th style="width:48px"></th></tr>'
        + lines
        + "</table></div>"
    )


def _detail_card(
    kind: Kind,
    job: Job,
    row: Row,
    finished: bool,
    conflicts: dict[int, set[int]],
    picked: set[int],
    by_id: dict[int, Row],
    groups: dict[str, list[Row]],
) -> str:
    finding = row.finding
    label = kind.label(finding)
    members = groups.get(row.grp, []) if row.grp else []
    where = f"{_file_label(job, row)}:{finding.line if finding.line is not None else '?'}"
    if finding.function:
        where += f" · {finding.function}()"
    mirror = ""
    if finished and row.id in conflicts:
        mirror = f'<label class="pick"><input type="checkbox" data-mirror="{row.id}"> 적용</label>'
    badge = _badge(kind.color(label), label)
    head = (
        f'<div class="head">{badge}<span class="check">{escape(finding.check)}</span>'
        f'<span class="where mono">{escape(where)}</span>'
        f'<div class="right">{_status_badge(row)}{mirror}</div></div>'
    )
    body = [f'<p class="detail">엑셀 {finding.row}행 · {escape(finding.detail)}</p>']
    if finding.note:
        body.append(f'<p class="note">{escape(finding.note)}</p>')
    others = [str(member.finding.row) for member in members if member.id != row.id]
    if others:
        body.append(f'<p class="excluded">함께 보낸 행: 엑셀 {", ".join(others)}행</p>')
    if row.result is not None:
        partner = _partner(row, conflicts, picked, by_id) if row.id not in picked else None
        if row.result.decision == "fix" and partner is not None:
            body.append(
                f'<p class="excluded">{_badge("gray", "이번엔 제외")} 행 {partner.finding.row} '
                "수정과 같은 줄을 고칩니다. 이번 패치를 적용하고 Polyspace를 다시 돌린 뒤 "
                "다시 분석하면 남은 문제만 다시 고칩니다.</p>"
            )
        if row.result.decision == "fix" and _edits_other_files(row):
            files = sorted({edit.file for edit in row.result.edits if edit.file})
            body.append(
                f'<p class="excluded">{_badge("blue", "다른 파일 수정")} '
                f"{escape(', '.join(files))}도 고칩니다. 그 함수를 부르는 모든 곳의 동작이 "
                '바뀌므로 "수정 모두 선택"이 고르지 않습니다. 보고 직접 체크하세요.</p>'
            )
        body.append(f'<div class="reason">{escape(row.result.reason)}</div>')
        # 같은 묶음의 수정 행은 수정이 똑같으므로 diff는 묶음의 첫 수정 행에만 보인다
        first_fix = next(
            (m for m in members if m.result is not None and m.result.decision == "fix"), None
        )
        if row.result.decision == "fix" and first_fix is not None and first_fix.id != row.id:
            body.append(
                f'<p class="excluded">같은 묶음의 수정입니다 '
                f"(엑셀 {first_fix.finding.row}행과 같음).</p>"
            )
        elif row.result.diff:
            body.append(_diff(row.result.diff))
    elif _kind(row) == "problem":
        summary, action, detail = _explain(row)
        more = ""
        if row.state == "error":
            log_row = min((m.finding.row for m in members), default=finding.row)
            log = f"대화 기록: private/llm-logs/ 안의 …_job{job.id}_row{log_row}.md"
            text = "\n".join(part for part in (detail, log) if part)
            more = f"<details><summary>자세히 (개발자용)</summary>{escape(text)}</details>"
        todo = f'<div class="todo">할 일: {escape(action)}</div>' if action else ""
        body.append(
            f'<div class="problem-box"><div class="what">{escape(summary)}</div>{todo}{more}</div>'
        )
    return (
        f'<div class="item band-{kind.color(label)}" id="row-{row.id}" data-kind="{_kind(row)}">'
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
    kind = KINDS[job.kind]
    finished = job.state != "running"
    when = job.created_at[5:16].replace("T", " ")
    body = (
        f"<h1>작업 {job.id} · {escape(job.excel_name)} {_job_badge(job, rows)}</h1>"
        f'<p class="sub">{escape(kind.title)} · 시트 {escape(job.sheet)} · {escape(when)} 시작'
        + (f" · 소스 폴더 {escape(job.source_root)}" if job.source_root else "")
        + f' · <a href="/results/{job.id}/summary">요약 (캡처용)</a></p>'
    )
    if job.note:
        body += f'<p class="sub">{escape(job.note)}</p>'
    if job.state == "running":
        done, total = _progress(rows)
        percent = round(done * 100 / total) if total else 100
        body += (
            f'<div class="progress"><div style="width:{percent}%"></div></div>'
            f'<p class="sub"><span class="spinner"></span>{done}/{total} 처리 · '
            "5초마다 새로 고칩니다</p>"
            f'<form class="retry" method="post" action="/results/{job.id}/stop">'
            '<button class="btn plain" type="submit">멈춤</button></form>'
        )
    if job.state == "stopped" and job.message == USER_STOP:
        body += (
            '<div class="notice paused">멈춘 작업입니다. '
            "'이어서 처리'를 누르면 남은 행을 처리합니다.</div>"
        )
    elif job.state == "stopped":
        body += f'<div class="notice">작업이 멈췄습니다: {escape(job.message)}</div>'
    if finished and (job.state == "stopped" or any(row.state == "error" for row in rows)):
        label = "이어서 처리" if job.state == "stopped" else "오류 행 다시 시도"
        body += (
            f'<form class="retry" method="post" action="/results/{job.id}/retry">'
            f'<button class="btn plain" type="submit">{label}</button></form>'
        )
    body += _chips(kind, job, rows)
    title = f"작업 {job.id} 결과 · {job.excel_name}"
    if not rows:
        body += '<div class="card"><p class="empty">분석할 행이 없습니다.</p></div>'
        return page(title, body, refresh=not finished)
    conflicts, picked, manual = _selection(kind, rows) if finished else ({}, set(), set())
    by_id = {row.id: row for row in rows}
    groups: dict[str, list[Row]] = {}
    for row in rows:
        if row.grp:
            groups.setdefault(row.grp, []).append(row)
    for members in groups.values():
        members.sort(key=lambda member: member.finding.row)
    cards = "".join(
        _detail_card(kind, job, row, finished, conflicts, picked, by_id, groups) for row in rows
    )
    overview = _overview(kind, job, rows, finished, conflicts, picked)
    panels = (
        '<div class="tabs"><button type="button" data-tab="overview" class="on">한눈에 보기'
        '</button><button type="button" data-tab="detail">자세히 보기 (diff)</button></div>'
        + _filters(rows)
        + f'<div data-panel="overview">{overview}</div>'
        + f'<div data-panel="detail" hidden>{cards}</div>'
    )
    if finished and conflicts:
        later = len(conflicts) - len(picked) - len(manual)
        note = f'<span class="later">같은 줄 겹침으로 {later}개는 다음 차례</span>' if later else ""
        if manual:
            hint = f"자동으로 고르지 않은 수정 {len(manual)}개는 직접 고르세요"
            note += f'<span class="later">{hint}</span>'
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


def _edits_other_files(row: Row) -> bool:
    """수정이 행의 파일 말고 다른 파일도 고치는지 (그 함수를 부르는 모든 곳에 영향)."""
    return row.result is not None and any(edit.file for edit in row.result.edits)


# 요약 페이지: 회사 밖으로 캡처해 보내는 화면이라 숫자와 정해 둔 글, 표준 용어만 찍는다.
# 엑셀·파일·함수 이름, 경로, 엑셀 칸의 글, LLM이 쓴 이유는 넣지 않는다
NOTE_NUMBERS = ("참고 정의", "호출자 조각", "못 읽은 파일")
OTHER_LABEL = "기타"
OTHER_CHECK = "기타 검사"
RULE_NUMBER = re.compile(r"(Rule|Dir)\s*\d+(\.\d+)*", re.I)
MAX_CHECKS = 12  # 검사 종류 표의 줄 수 (나머지는 한 줄로 합친다)


def _summary_check(check: str) -> str:
    """검사 이름을 찍어도 되는 모양으로: 규칙 번호, 표준 용어로만 된 이름, 아니면 기타."""
    found = RULE_NUMBER.match(check.strip())
    if found:
        return found.group(0)
    shown = safe(check)
    return shown if shown == check else OTHER_CHECK


def _count_table(title: str, groups: dict[str, Counter], keys: list[str]) -> str:
    head = "".join(f'<th class="num">{KIND_LABELS[key]}</th>' for key in keys)
    lines = "".join(
        f'<tr><td>{escape(name)}</td><td class="num">{counts["all"]}</td>'
        + "".join(f'<td class="num">{counts[key]}</td>' for key in keys)
        + "</tr>"
        for name, counts in groups.items()
    )
    return (
        f'<div class="box"><h3>{title}</h3><table><tr><th></th><th class="num">전체</th>'
        f"{head}</tr>{lines}</table></div>"
    )


def summary_page(job: Job, rows: list[Row]) -> str:
    kind = KINDS[job.kind]
    finished = job.state != "running"
    keys = [key for key in KIND_ORDER if key != "waiting" or any(_kind(r) == key for r in rows)]
    by_label: dict[str, Counter] = {label: Counter() for label in kind.choices}
    by_check: dict[str, Counter] = {}
    for row in rows:
        label = kind.label(row.finding)
        decided = _kind(row)
        for counts in (
            by_label.setdefault(label if label in kind.choices else OTHER_LABEL, Counter()),
            by_check.setdefault(_summary_check(row.finding.check), Counter()),
        ):
            counts["all"] += 1
            counts[decided] += 1
    total = sum(by_label.values(), Counter())
    total["all"] = len(rows)
    by_label["합계"] = total
    checks = sorted(by_check.items(), key=lambda item: (-item[1]["all"], item[0]))
    if len(checks) > MAX_CHECKS:
        rest = sum((counts for _, counts in checks[MAX_CHECKS - 1 :]), Counter())
        checks = [*checks[: MAX_CHECKS - 1], (f"그 밖 {len(checks) - MAX_CHECKS + 1}종", rest)]

    facts = [("상태", JOB_STATES.get(job.state, job.state))]
    if job.state == "running":
        done, all_rows = _progress(rows)
        facts.append(("처리", f"{done}/{all_rows}"))
    mode = next((label for _, label, _ in kind.modes if job.note.startswith(label)), "")
    if mode:
        facts.append(("처리 방식", mode))
    facts.append(("제외한 행", str(job.skipped)))
    for name in NOTE_NUMBERS:
        found = re.search(rf"{name} (\d+)개", job.note)
        facts.append((name, found.group(1) if found else "0"))
    problems = Counter(row.state for row in rows if _kind(row) == "problem")
    facts += [(ROW_STATES[state], str(count)) for state, count in sorted(problems.items())]
    if finished:
        conflicts, picked, manual = _selection(kind, rows) if rows else ({}, set(), set())
        fixes = [r for r in rows if r.result is not None and r.result.decision == "fix"]
        facts += [
            ("수정 모두 선택이 고르는 수정", str(len(picked))),
            ("같은 줄 겹침으로 다음 차례", str(len(conflicts) - len(picked) - len(manual))),
            ("직접 골라야 하는 수정", str(len(manual))),
            ("다른 파일 수정", str(sum(_edits_other_files(r) for r in fixes))),
        ]
    fact_rows = "".join(
        f'<tr><td>{escape(name)}</td><td class="num">{escape(value)}</td></tr>'
        for name, value in facts
    )
    body = (
        f'<div class="summary-page"><h1>작업 {job.id} 요약 · {escape(kind.title)}</h1>'
        '<p class="sub">숫자와 표준 용어만 보입니다(엑셀·파일·함수 이름, 경로, 코드, 이유 없음). '
        f'이 화면은 그대로 캡처해 보내도 됩니다. <a href="/results/{job.id}">결과 화면으로</a></p>'
        '<div class="grid2">'
        + _count_table("대상별 판단", by_label, keys)
        + f'<div class="box"><h3>작업</h3><table>{fact_rows}</table></div></div>'
        + _count_table("검사 종류별", dict(checks), keys)
        + "</div>"
    )
    return page(f"작업 {job.id} 요약", body, refresh=not finished)
