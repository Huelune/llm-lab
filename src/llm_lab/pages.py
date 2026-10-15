"""서비스 화면 HTML. 페이지가 몇 개뿐이라 템플릿 엔진 없이 문자열로 만든다.

외부 CSS·스크립트·글꼴을 쓰지 않는다 (브라우저도 바깥으로 요청하지 않게).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from html import escape
from urllib.parse import quote

from llm_lab.config import CLIENT_KINDS
from llm_lab.connections import Connection
from llm_lab.kinds import KINDS, Kind
from llm_lab.patch import APPLY_COMMAND, find_conflicts, pick_without_conflicts
from llm_lab.safe_text import safe
from llm_lab.sources import base_name
from llm_lab.store import USER_STOP, Job, Row

APP_NAME = "LLM Test Advisor"
APP_TAGLINE = "정적 분석 결과와 소스 코드를 묶어 LLM에 묻고, 판단과 수정안을 받습니다."
JOB_STATES = {"running": "처리 중", "done": "완료", "stopped": "멈춤"}
JOB_BADGES = {"running": "blue", "done": "green", "stopped": "red"}
ROW_STATES = {
    "pending": "대기",
    "running": "처리 중",
    "done": "완료",
    "error": "오류",
    "no_source": "소스 없음",
}
DECISIONS = {"fix": "수정안", "no_fix": "수정 불필요", "manual": "직접 수정"}
# 화면의 분류. "직접 수정"은 LLM이 고쳐야 한다고 봤지만 사람이 정해야 하는 행(diff 없음),
# "확인 필요"는 오류와 소스 없음처럼 사용자가 손봐야 하는 행이다
KIND_LABELS = {
    "fix": "수정안",
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
  --bg: #f5f7fa; --card: #fff; --line: #e4e8ee; --line-strong: #cfd6e0; --text: #18202e;
  --muted: #5f6b7c; --faint: #8a94a6; --brand: #2459c9; --brand-dark: #1b46a3;
  --brand-bg: #ebf1fd; --ink: #18202e; --red: #c0262d; --red-bg: #fdecec;
  --orange: #b25600; --orange-bg: #fff1e2; --green: #1a7f43; --green-bg: #e5f5ec;
  --gray-bg: #eef1f5; --amber: #8a5a00; --amber-bg: #fff6da; --add: #e3f6e8; --del: #fde9eb;
  --radius: 6px; --shadow: none;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 "Malgun Gothic", "맑은 고딕", "Segoe UI", sans-serif; }
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
code, .mono { font-family: Consolas, "D2Coding", monospace; }
.mono { font-size: 13px; }
.topbar { background: var(--ink); color: #fff; }
.topbar .inner { max-width: 1280px; margin: 0 auto; padding: 10px 24px; display: flex;
  gap: 20px; align-items: center; }
.topbar .name { font-weight: 700; margin-right: auto; color: #fff; display: flex; gap: 8px;
  align-items: center; }
.topbar .name img { width: 20px; height: 20px; }
.topbar a { color: #c4cddc; font-size: 13px; }
.topbar a:hover { color: #fff; text-decoration: none; }
.topbar a.conn { background: var(--brand); color: #fff; border-radius: 999px; padding: 2px 12px;
  max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topbar a.conn:hover { background: var(--brand-dark); }
main { max-width: 1280px; margin: 0 auto; padding: 20px 24px 0; }
h1 { font-size: 20px; margin: 0; display: flex; gap: 10px; align-items: center; }
h2 { font-size: 15px; margin: 24px 0 10px; }
.sub { color: var(--muted); font-size: 12.5px; margin: 4px 0 12px; }
.app-title { margin-bottom: 2px; }
.pagehead { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.pagehead > div:first-child { flex: 1 1 320px; min-width: 0; }
.pagehead .sub { overflow-wrap: anywhere; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 14px 18px; margin-bottom: 12px; box-shadow: var(--shadow); }
.card.table { padding: 0; overflow: hidden; }
.step { display: flex; gap: 14px; align-items: flex-start; padding: 14px 0;
  border-bottom: 1px solid var(--line); }
.step:first-child { padding-top: 4px; }
.step:last-child { border-bottom: 0; padding-bottom: 4px; }
.step .num { flex: none; width: 22px; height: 22px; border-radius: 4px; background: var(--gray-bg);
  color: var(--brand); display: grid; place-items: center; font-weight: 700; font-size: 13px; }
.step > div + div { flex: 1; min-width: 0; }
.step h3 { margin: 2px 0; font-size: 14.5px; }
.hint { color: var(--muted); font-size: 12.5px; margin: 0 0 8px; }
input[type=text], input[type=password], select { font: 13px Consolas, "D2Coding", monospace;
  padding: 7px 10px; width: 100%; border: 1px solid var(--line-strong); border-radius: 8px;
  background: #fff; }
input[type=text]:focus, input[type=password]:focus, select:focus { border-color: var(--brand);
  outline: 3px solid var(--brand-bg); outline-offset: 0; }
input[type=file] { font-size: 13px; padding: 10px; border: 1px dashed var(--line-strong);
  border-radius: 8px; width: 100%; background: #fafbfd; }
input[type=checkbox], input[type=radio] { accent-color: var(--brand); }
.pickrow { display: flex; gap: 6px; }
.pickrow input { flex: 1; }
.pickrow .btn { white-space: nowrap; }
#pick-note { margin: 4px 0 0; }
.btn { display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 13px;
  font-weight: 700; border: 0; border-radius: 8px; padding: 7px 14px; cursor: pointer;
  line-height: 1.3; }
.btn:hover { text-decoration: none; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover { background: var(--brand-dark); }
.btn.primary:disabled { background: #9aa4b5; cursor: not-allowed; }
.btn.ghost { background: transparent; color: #fff; border: 1px solid #4d5873; font-weight: 400; }
.btn.ghost:hover { background: rgba(255, 255, 255, .08); }
.btn.plain { background: #fff; color: var(--text); border: 1px solid var(--line-strong);
  font-weight: 400; }
.btn.plain:hover { background: var(--gray-bg); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-weight: 400; font-size: 12px; background: #f8f9fb; }
td.num, th.num { text-align: right; width: 64px; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.card.table tr:hover td { background: #fafbfd; }
.list td:last-child, .list th { white-space: nowrap; }
.badge { display: inline-block; padding: 0 7px; border-radius: 4px; font-size: 12px;
  font-weight: 700; line-height: 20px; white-space: nowrap; }
.badge.red { color: var(--red); background: var(--red-bg); }
.badge.orange { color: var(--orange); background: var(--orange-bg); }
.badge.green { color: var(--green); background: var(--green-bg); }
.badge.gray { color: var(--muted); background: var(--gray-bg); }
.badge.blue { color: var(--brand); background: var(--brand-bg); }
.badge.amber { color: var(--amber); background: var(--amber-bg); }
.chips { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center;
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 10px 16px; margin-bottom: 14px; box-shadow: var(--shadow); }
.chips b { font-size: 16px; font-variant-numeric: tabular-nums; margin-left: 2px; }
.chips .sep { width: 1px; height: 20px; background: var(--line); color: transparent;
  overflow: hidden; }
.progress { height: 6px; background: var(--gray-bg); border-radius: 999px; overflow: hidden;
  margin: 8px 0 4px; }
.progress div { height: 100%; background: var(--brand); }
.spinner { display: inline-block; width: 14px; height: 14px; margin-right: 6px;
  vertical-align: -2px; border: 2px solid var(--gray-bg); border-top-color: var(--brand);
  border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
.notice { background: var(--red-bg); color: var(--red); border-radius: 8px; padding: 8px 12px;
  margin: 8px 0; }
.notice.paused { background: var(--gray-bg); color: var(--text); }
.kind { margin: 4px 0; }
.modes { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 2px 0; }
.choices { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 4px 0 8px 22px;
  padding: 8px 12px; background: #f8f9fb; border-radius: 8px; font-size: 13px; }
.choices .modes, .choices .hint { flex-basis: 100%; }
.choices .hint { margin: 0; }
.retry { margin: 8px 0 12px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: flex-end;
  border-bottom: 1px solid var(--line); margin: 0 0 12px; }
.tabs { display: flex; gap: 2px; margin-right: auto; }
.tabs button { font: inherit; font-weight: 700; color: var(--muted); background: none;
  border: 0; border-bottom: 2px solid transparent; padding: 8px 14px; cursor: pointer;
  margin-bottom: -1px; }
.tabs button:hover { color: var(--text); }
.tabs button.on { color: var(--brand); border-bottom-color: var(--brand); }
.filters { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 8px; }
.filters button { font: inherit; font-size: 12px; border: 1px solid var(--line-strong);
  background: #fff; border-radius: 4px; padding: 3px 10px; cursor: pointer;
  color: var(--muted); }
.filters button:hover { color: var(--text); }
.filters button.on { background: var(--ink); color: #fff; border-color: var(--ink); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
@media (max-width: 800px) { .grid2 { grid-template-columns: 1fr; } }
.box { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 8px 6px; box-shadow: var(--shadow); }
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
.box h3 { font-size: 12px; margin: 2px 8px 6px; color: var(--muted); }
/* 집계 표의 행은 눌러서 거른다. 고른 행은 파랗게 남는다 */
.box tr[data-pick] { cursor: pointer; }
.box tr[data-pick]:hover td { background: var(--gray-bg); }
.box tr[data-pick].on td { background: var(--brand-bg); color: var(--brand); font-weight: 700; }
.filters .shown { color: var(--muted); font-size: 12px; align-self: center; }
/* 칸 폭을 고정해 긴 요약·검사·위치는 줄이고, 맨 끝 "보기"가 카드 밖으로 밀리지 않게 한다 */
.list table { table-layout: fixed; }
.list td.summary, .list td.mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.list td.summary { color: var(--muted); }
.list tr.problem td.summary { color: var(--amber); }
.list td.check { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.later { color: #c4cddc; font-size: 12px; }
/* 자세히 보기 카드: 머리줄(대상·검사·위치·판단)과 본문(근거, diff)을 나눈다 */
.item { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--line);
  border-radius: var(--radius); margin-bottom: 10px; box-shadow: var(--shadow); overflow: hidden; }
.item.band-red { border-left-color: var(--red); }
.item.band-orange { border-left-color: #f08c1a; }
.item.band-gray { border-left-color: var(--line-strong); }
.item .head { display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: center;
  padding: 8px 14px; background: #fafbfc; border-bottom: 1px solid var(--line); }
.item .check { font-weight: 700; }
.item .where { color: var(--muted); }
.item .right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.pick { display: flex; gap: 4px; align-items: center; cursor: pointer; font-size: 13px; }
.item .body { padding: 10px 14px 12px; }
.item .body > :last-child { margin-bottom: 0; }
.detail { color: var(--muted); font-size: 12.5px; margin: 0 0 8px; }
.item.hit { outline: 2px solid var(--brand); outline-offset: 2px; }  /* "보기"로 찾아온 카드 */
.reason { background: var(--brand-bg); border-left: 3px solid var(--brand); padding: 7px 12px;
  border-radius: 6px; margin: 0 0 10px; }
.excluded { color: var(--muted); font-size: 12.5px; margin: 0 0 8px; }
.note { color: var(--amber); font-size: 12.5px; margin: 0 0 8px; }
.problem-box { background: var(--amber-bg); border-left: 3px solid #e0a400; padding: 8px 12px;
  border-radius: 6px; }
.problem-box .what { font-weight: 700; color: var(--amber); }
.problem-box .todo { margin-top: 2px; }
.problem-box details { margin-top: 6px; color: var(--muted); font-size: 12px;
  white-space: pre-wrap; }
.error { background: var(--red-bg); color: var(--red); padding: 8px 12px; border-radius: 8px; }
pre.diff { margin: 0; background: #fbfcfd; border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 0; overflow: auto; font: 12.5px/1.5 Consolas, "D2Coding", monospace;
  max-height: 480px; }
pre.diff span { display: block; padding: 0 12px 0 9px; white-space: pre;
  border-left: 3px solid transparent; }
pre.diff .add { background: var(--add); border-left-color: var(--green); }
pre.diff .del { background: var(--del); border-left-color: var(--red); }
pre.diff .hunk { color: var(--faint); }
pre.diff span::before { content: attr(data-ln); color: var(--faint); margin-right: 12px;
  user-select: none; }
.howto { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 10px 16px; margin: 12px 0; font-size: 13px; box-shadow: var(--shadow); }
.howto ol { margin: 6px 0 0; padding-left: 20px; }
.howto details { margin-top: 8px; border-top: 1px solid var(--line); padding-top: 8px; }
.howto summary { cursor: pointer; color: var(--muted); }
.cmd { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
.cmd code { flex: 1; background: var(--ink); color: #e6edf7; padding: 5px 10px; border-radius: 6px;
  font-size: 12.5px; overflow-x: auto; white-space: nowrap; }
.cmd button { font: inherit; font-size: 12px; border: 1px solid var(--line-strong);
  background: #fff; border-radius: 6px; padding: 3px 10px; cursor: pointer; }
.cmd button:hover { background: var(--gray-bg); }
.folder { background: var(--gray-bg); padding: 1px 6px; border-radius: 4px; }
.applybar { position: sticky; bottom: 0; background: var(--ink); color: #fff;
  border-radius: var(--radius) var(--radius) 0 0; padding: 10px 16px; display: flex; gap: 10px;
  align-items: center; flex-wrap: wrap; margin-top: 12px; font-size: 13px;
}
.applybar .count { font-weight: 700; margin-right: auto; }
.empty { color: var(--muted); padding: 14px 16px; margin: 0; }
/* 분석 전 확인 결과: 줄마다 종류(막음·주의·확인됨)를 글자로도 밝힌다 */
.precheck { margin-top: 10px; border: 1px solid var(--line); border-radius: var(--radius);
  padding: 8px 12px; font-size: 13px; background: #fafbfc; }
.precheck div { padding: 1px 0; }
.precheck .bad { color: var(--red); }
.precheck .bad::before { content: "막음 · "; font-weight: 700; }
.precheck .warn { color: var(--amber); }
.precheck .warn::before { content: "주의 · "; font-weight: 700; }
.precheck .ok::before { content: "확인 · "; font-weight: 700; color: var(--green); }
.precheck .info { color: var(--muted); }
.precheck .hint { margin: 4px 0 0; }
.card.warn { background: var(--amber-bg); color: var(--amber); border-color: #f0d68a; }
/* 설정: 연결마다 한 줄 카드, 현재 연결은 왼쪽 띠, 추가·수정 양식은 두 칸 */
.connrow { display: flex; gap: 8px 14px; align-items: center; flex-wrap: wrap;
  border-left: 4px solid var(--line); }
.connrow.current { border-left-color: var(--brand); }
.connrow .what { flex: 1 1 320px; min-width: 0; }
.connrow .what .sub { margin: 2px 0 0; overflow-wrap: anywhere; }
.connrow .what .sub.ok { color: var(--green); }
.connrow .what .sub.bad { color: var(--red); }
.connrow .row-actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.connrow .row-actions .hint { margin: 0; }
.fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; margin: 8px 0 12px; }
.fields label { display: block; font-size: 12.5px; color: var(--muted); }
.fields label input, .fields label select { margin-top: 3px; color: var(--text); }
@media (max-width: 700px) { .fields { grid-template-columns: 1fr; } }
/* 작업 목록: 이름(링크)·종류·연결·모델·시작·대상·판단·진행·결과 보기, 대상과 판단은 배지 묶음 */
.jobs td { vertical-align: middle; padding-top: 9px; padding-bottom: 9px; }
.jobs .jobname a { font-weight: 700; color: var(--text); overflow-wrap: anywhere; }
.jobs .tags { line-height: 26px; }
.jobs .go { text-align: right; white-space: nowrap; }
/* 좁은 화면에서는 연결·모델·시작, 더 좁으면 종류·대상·결과 보기도 숨긴다
   (이름·판단·진행은 남고, 이름이 결과로 가는 링크다) */
@media (max-width: 900px) {
  .jobs th:nth-child(3), .jobs td:nth-child(3), .jobs th:nth-child(4), .jobs td:nth-child(4),
  .jobs th:nth-child(5), .jobs td:nth-child(5) { display: none; }
}
@media (max-width: 560px) {
  .jobs th:nth-child(2), .jobs td:nth-child(2), .jobs th:nth-child(6), .jobs td:nth-child(6),
  .jobs th:nth-child(9), .jobs td:nth-child(9) { display: none; }
}
/* 작업 화면 머리: 제목 아래 접힌 이름 바꾸기 */
.pagehead .rename { margin: 2px 0 6px; font-size: 13px; }
.pagehead .rename summary { cursor: pointer; color: var(--muted); }
.pagehead .rename form { display: flex; gap: 6px; margin-top: 6px; max-width: 420px; }
.pagehead .rename input { flex: 1 1 auto; min-width: 0; }
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
  if (count) count.textContent = `적용할 수정안 ${n}개 선택됨 (수정안 ${boxes.length}개 중)`;
  const allPicked = auto.length && auto.every(b => b.checked);
  if (toggle) toggle.textContent = allPicked ? '모두 해제' : '수정안 모두 선택';
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
// 걸러 보기: 판단 단추, 검사·파일(집계 표의 행)을 함께 건다. 다시 읽은 뒤에도 남는다
const filters = [...document.querySelectorAll('[data-filter]')];
const picks = [...document.querySelectorAll('[data-pick]')];
const shown = document.getElementById('shown');
const reset = document.getElementById('filter-reset');
const everything = {kind: 'all', check: '', file: ''};
let view = {...everything};
function applyFilter() {
  filters.forEach(b => b.classList.toggle('on', b.dataset.filter === view.kind));
  picks.forEach(r => r.classList.toggle('on', view[r.dataset.pick] === r.dataset.value));
  let n = 0, all = 0;
  document.querySelectorAll('[data-kind]').forEach(item => {
    item.hidden = (view.kind !== 'all' && item.dataset.kind !== view.kind)
      || (view.check !== '' && item.dataset.check !== view.check)
      || (view.file !== '' && item.dataset.file !== view.file);
    if (item.tagName === 'TR') { all++; if (!item.hidden) n++; }
  });
  const narrowed = view.kind !== 'all' || view.check !== '' || view.file !== '';
  if (shown) shown.textContent = narrowed ? `표시 ${n} / 전체 ${all}` : '';
  if (reset) reset.hidden = !narrowed;
  remember('filter', JSON.stringify(view));
}
filters.forEach(b => b.addEventListener('click', () => {
  view.kind = b.dataset.filter;
  applyFilter();
}));
picks.forEach(r => {
  const pick = () => {
    const key = r.dataset.pick;
    view[key] = view[key] === r.dataset.value ? '' : r.dataset.value;
    applyFilter();
  };
  r.addEventListener('click', pick);
  r.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
  });
});
if (reset) reset.addEventListener('click', () => { view = {...everything}; applyFilter(); });
try {
  const saved = JSON.parse(recall('filter') || 'null');
  if (saved && typeof saved === 'object') view = {...everything, ...saved};
} catch (e) {}
if (filters.length) applyFilter();
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
// 뒤로 가기·새로 고침이면 브라우저가 고른 종류만 되살린다. 그 종류의 칸을 맞춰 보인다
function syncChoices() {
  const chosen = document.querySelector('input[name=kind]:checked');
  if (chosen) showChoices(chosen.value);
}
syncChoices();
window.addEventListener('pageshow', syncChoices);
// 분석 전 확인: 서버가 엑셀과 폴더를 읽어 보고 개수만 돌려준다 (작업은 만들지 않는다)
const precheck = document.getElementById('precheck');
const precheckResult = document.getElementById('precheck-result');
function showCheck(items, note) {
  precheckResult.hidden = false;
  precheckResult.replaceChildren();
  items.forEach(([level, text]) => {
    const line = document.createElement('div');
    line.className = level;
    line.textContent = text;
    precheckResult.append(line);
  });
  if (note) {
    const line = document.createElement('div');
    line.className = 'hint';
    line.textContent = note;
    precheckResult.append(line);
  }
}
if (precheck && analyze) {
  precheck.addEventListener('click', async () => {
    precheck.disabled = true;
    showCheck([['info', '엑셀과 폴더를 읽는 중…']]);
    try {
      const response = await fetch('/check', {method: 'POST', body: new FormData(analyze)});
      const data = await response.json();
      const items = [
        ...data.errors.map(text => ['bad', text]),
        ...data.warnings.map(text => ['warn', text]),
        ...data.facts.map(text => ['ok', text]),
      ];
      showCheck(items, data.errors.length ? '고친 뒤 다시 확인하세요.' : '');
    } catch (e) {
      showCheck([['bad', '확인하지 못했습니다. 서버가 켜져 있는지 보세요.']]);
    } finally {
      precheck.disabled = false;
    }
  });
  // 입력을 바꾸면 지난 확인 결과는 맞지 않으므로 지운다
  analyze.addEventListener('change', () => { precheckResult.hidden = true; });
}
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


def page(title: str, body: str, *, refresh: bool = False, connection: str | None = None) -> str:
    """refresh면 스크립트가 5초마다 내용 지문(data-version)을 확인해 바뀌었을 때만 다시 읽는다.

    connection이 None이 아니면 위쪽 막대 오른쪽에 현재 연결 이름(""이면 없음)을 보인다.
    """
    main = "<main>"
    if refresh:
        version = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        main = f'<main data-refresh="5" data-version="{version}">'
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="icon" href="{FAVICON_HREF}">'
        f"<title>{escape(title)} · {APP_NAME}</title><style>{STYLE}</style></head><body>"
        f'<div class="topbar"><div class="inner"><a class="name" href="/">'
        f'<img src="{FAVICON_HREF}" alt="">{APP_NAME}</a>'
        '<a href="/">새 분석</a><a href="/results">작업 목록</a>'
        f"{_connection_link(connection)}</div></div>"
        f"{main}{body}</main><script>{SCRIPT}</script></body></html>"
    )


def _connection_link(connection: str | None) -> str:
    if connection is None:
        return ""
    return f'<a class="conn" href="/settings">연결: {escape(connection or "없음")}</a>'


def error_page(title: str, message: str, *, connection: str | None = None) -> str:
    body = (
        f"<h1>{escape(title)}</h1>"
        f'<div class="card"><div class="error">{escape(message)}</div>'
        '<p><a href="/">새 분석으로 돌아가기</a></p></div>'
    )
    return page(title, body, connection=connection)


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


def _row_states(rows: list[Row]) -> str:
    """처리 중 작업의 행 상태별 개수와 지금 처리 중인 엑셀 행 (DB에 있는 값만)."""
    states = Counter(row.state for row in rows)
    parts = [
        f"{ROW_STATES[state]} {states[state]}"
        for state in ROW_STATES
        if states[state] or state in ("pending", "running", "done")
    ]
    running = sorted(row.finding.row for row in rows if row.state == "running")
    if running:
        parts.append(f"지금 엑셀 {', '.join(str(number) for number in running)}행")
    return " · ".join(parts)


def _job_badge(job: Job, rows: list[Row]) -> str:
    label = JOB_STATES.get(job.state, job.state)
    if job.state == "running":
        done, total = _progress(rows)
        label = f"{label} {done}/{total}"
    return _badge(JOB_BADGES.get(job.state, "gray"), label)


def _jobs_table(items: list[tuple[Job, list[Row]]]) -> str:
    if not items:
        return (
            '<div class="card table"><p class="empty">아직 작업이 없습니다. '
            '<a href="/">새 분석</a>에서 엑셀을 올리면 여기에 쌓입니다.</p></div>'
        )
    lines = []
    for job, rows in items:
        kind = KINDS[job.kind]
        labels = " ".join(
            _badge(kind.color(label), f"{label} {count}")
            for label, count in _label_counts(kind, rows)
        )
        decided = Counter(_kind(row) for row in rows)
        verdicts = " ".join(
            _badge(KIND_BADGES[key], f"{KIND_LABELS[key]} {decided[key]}")
            for key in KIND_ORDER
            if decided[key]
        )
        when = job.created_at[5:16].replace("T", " ")
        model = _settings(job).get("model") or "-"
        lines.append(
            f'<tr><td class="jobname"><a href="/results/{job.id}">'
            f"{escape(job.title or job.excel_name)}</a></td>"
            f"<td>{escape(kind.title)}</td><td>{escape(job.connection or '-')}</td>"
            f"<td>{escape(str(model))}</td><td>{escape(when)}</td>"
            f'<td class="tags">{labels}</td><td class="tags">{verdicts}</td>'
            f"<td>{_job_badge(job, rows)}</td>"
            f'<td class="go"><a href="/results/{job.id}">결과 보기 →</a></td></tr>'
        )
    return (
        '<div class="card table jobs"><table><tr><th>이름</th><th>종류</th><th>연결</th>'
        "<th>모델</th><th>시작</th><th>대상</th><th>판단</th>"
        '<th style="width:110px">진행</th><th style="width:96px"></th></tr>'
        + "".join(lines)
        + "</table></div>"
    )


def _settings(job: Job) -> dict:
    """작업을 만들 때 기록한 설정. 비었거나 깨졌으면 빈 사전이다."""
    try:
        data = json.loads(job.settings) if job.settings else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _settings_card(job: Job) -> str:
    settings = _settings(job)
    if not settings:
        return (
            '<div class="card"><h3>작업 설정</h3><p class="empty">기록된 설정이 없습니다.</p></div>'
        )
    kind = KINDS.get(job.kind)
    modes = {value: label for value, label, _ in kind.modes} if kind else {}
    choices = settings.get("choices")
    follow_ups = settings.get("follow_ups")
    facts = [
        ("연결", settings.get("connection")),
        ("모델", settings.get("model")),
        ("시트 종류", kind.title if kind else settings.get("kind")),
        ("처리 방식", modes.get(settings.get("mode"), settings.get("mode"))),
        ("분석 대상", ", ".join(map(str, choices)) if isinstance(choices, list) else None),
        ("되묻기 횟수", None if follow_ups is None else str(follow_ups)),
        ("소스 폴더", settings.get("source_root")),
        ("엑셀 파일", settings.get("excel")),
        ("시작 시각", job.created_at[:16].replace("T", " ")),
    ]
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{escape(str(value)) if value else '-'}</td></tr>"
        for name, value in facts
    )
    return f'<div class="card"><h3>작업 설정</h3><table>{rows}</table></div>'


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


def index_page(
    recent: list[tuple[Job, list[Row]]],
    recent_dirs: list[str],
    *,
    connection: str | None = None,
) -> str:
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
        '<p class="hint">"확인"은 작업을 만들지 않고 엑셀의 대상 행 수와 폴더에서 찾은 파일 수를 '
        "미리 봅니다. 분석 시작을 누르면 결과 화면으로 넘어가고, 행이 하나씩 처리되는 모습을 볼 수 "
        "있습니다</p>"
        '<div class="actions"><button class="btn plain" type="button" id="precheck">확인</button>'
        '<button class="btn primary" type="submit">분석 시작</button></div>'
        '<div class="precheck" id="precheck-result" aria-live="polite" hidden></div>'
        "</div></div></form>"
    )
    body = (
        f'<h1 class="app-title">{APP_NAME}</h1><p class="sub">{APP_TAGLINE}</p><h2>새 분석</h2>'
        '<p class="sub">Polyspace 결과 엑셀과 소스 폴더를 주면 고른 시트의 행마다 '
        "LLM이 수정안을 만듭니다.</p>"
        + (
            '<div class="card warn">설정에서 LLM 연결을 추가하세요 '
            '<a href="/settings">설정</a></div>'
            if connection == ""
            else ""
        )
        + form
        + '<h2>최근 작업 <a href="/results" style="font-size:12px;font-weight:400">'
        "전체 보기</a></h2>" + _jobs_table(recent)
    )
    return page("새 분석", body, connection=connection)


def results_page(items: list[tuple[Job, list[Row]]], *, connection: str | None = None) -> str:
    body = (
        '<div class="pagehead"><div><h1>작업 목록</h1>'
        f'<p class="sub">작업 {len(items)}개 · 최근 작업부터 보여줍니다.</p></div>'
        '<div class="actions"><a class="btn primary" href="/">새 분석</a></div></div>'
        + _jobs_table(items)
    )
    return page("작업 목록", body, connection=connection)


HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _diff(diff: str) -> str:
    """diff를 화면용으로. 줄마다 원본·수정본 줄 번호(data-ln)를 단다 (머리 줄은 빈칸)."""
    lines = []
    old = new = old_left = new_left = 0
    for line in diff.split("\n"):
        kind, left, right = "", "", ""
        hunk = HUNK.match(line)
        if hunk:
            kind = "hunk"
            old, new = int(hunk[1]), int(hunk[3])
            old_left = int(hunk[2]) if hunk[2] is not None else 1
            new_left = int(hunk[4]) if hunk[4] is not None else 1
        elif old_left <= 0 and new_left <= 0:
            kind = "hunk"  # --- a/…, +++ b/… 파일 머리 (파일이 여럿이면 여러 번)
        elif line.startswith("+"):
            kind, right = "add", str(new)
            new, new_left = new + 1, new_left - 1
        elif line.startswith("-"):
            kind, left = "del", str(old)
            old, old_left = old + 1, old_left - 1
        elif not line.startswith("\\"):  # "\ No newline at end of file"은 번호가 없다
            left, right = str(old), str(new)
            old, new, old_left, new_left = old + 1, new + 1, old_left - 1, new_left - 1
        css = f' class="{kind}"' if kind else ""
        lines.append(f'<span{css} data-ln="{left:>5}{right:>5}">{escape(line)}</span>')
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


def _group_table(title: str, groups: dict[str, Counter], mono: bool, field: str) -> str:
    css = ' class="mono"' if mono else ""
    lines = []
    for name, counts in sorted(groups.items(), key=lambda item: (-item[1]["all"], item[0])):
        cells = "".join(
            f'<td class="num">{counts[key]}</td>'
            for key in ("all", "fix", "nofix", "manual", "problem")
        )
        # 긴 이름(MISRA 규칙 문장)은 CSS가 한 줄로 줄이므로 전체는 title로 보인다
        lines.append(
            f'<tr data-pick="{field}" data-value="{escape(name)}" tabindex="0">'
            f'<td{css} title="{escape(name)}">{escape(name)}</td>{cells}</tr>'
        )
    return (
        f'<div class="box"><h3>{title}</h3><table><tr><th></th><th class="num">전체</th>'
        '<th class="num">수정안</th><th class="num">불필요</th><th class="num">직접 수정</th>'
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
        + _group_table("검사 종류별", by_check, mono=False, field="check")
        + _group_table("파일별", by_file, mono=True, field="file")
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
    # 표시 건수와 초기화는 스크립트가 채운다 (검사·파일은 집계 표의 행을 눌러 고른다)
    html.append(
        '<span class="shown" id="shown"></span>'
        '<button type="button" id="filter-reset" hidden>필터 초기화</button>'
    )
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
        f'<tr data-kind="{kind}" data-check="{escape(finding.check)}" '
        f'data-file="{escape(_file_label(job, row))}"{css}><td>{box}</td><td>{finding.row}</td>'
        f"<td>{badge}</td>"
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
                '바뀌므로 "수정안 모두 선택"이 고르지 않습니다. 보고 직접 체크하세요.</p>'
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
        f'<div class="item band-{kind.color(label)}" id="row-{row.id}" data-kind="{_kind(row)}" '
        f'data-check="{escape(finding.check)}" data-file="{escape(_file_label(job, row))}">'
        f'{head}<div class="body">{"".join(body)}</div></div>'
    )


def _howto(base: str | None) -> str:
    if base is None:
        folder = "소스 파일이 있는 폴더"
    elif base == "":
        folder = "엑셀 File 경로가 시작되는 폴더"
    else:
        folder = base.replace("/", "\\")

    def steps(flag: str) -> str:
        return "".join(
            f'<li><div class="cmd"><code>{escape(command)}</code>'
            f'<button type="button" data-copy="{escape(command)}">복사</button></div></li>'
            for command in (
                f"{APPLY_COMMAND}{flag} --check fixes.patch",
                f"{APPLY_COMMAND}{flag} fixes.patch",
            )
        )

    return (
        '<div class="howto"><b>패치 적용 방법</b> — 내려받은 fixes.patch를 아래 폴더에 두고 '
        f'차례로 실행하세요.<ol><li>폴더: <code class="folder">{escape(folder)}</code></li>'
        f"{steps('')}</ol>"
        '<p class="hint">--check 통과는 패치를 적용할 수 있다는 뜻일 뿐입니다. 위반이 '
        "없어졌는지는 Polyspace를 다시 돌려 확인하세요.</p>"
        "<details><summary>되돌리기</summary>적용한 것과 같은 fixes.patch로, 같은 폴더에서 "
        "차례로 실행하면 패치 전으로 돌아갑니다. 적용한 뒤 그 줄을 손으로 고쳤으면 확인 단계에서 "
        f"멈춥니다.<ol>{steps(' -R')}</ol></details></div>"
    )


def job_page(job: Job, rows: list[Row], base: str | None, *, connection: str | None = None) -> str:
    kind = KINDS[job.kind]
    finished = job.state != "running"
    when = job.created_at[5:16].replace("T", " ")
    note = f'<p class="sub">{escape(job.note)}</p>' if job.note else ""
    body = (
        '<div class="pagehead"><div>'
        f"<h1>{escape(job.title or job.excel_name)} {_job_badge(job, rows)}</h1>"
        '<details class="rename"><summary>이름 바꾸기</summary>'
        f'<form method="post" action="/results/{job.id}/rename">'
        f'<input type="text" name="title" value="{escape(job.title)}" maxlength="80" required> '
        '<button class="btn plain" type="submit">저장</button></form></details>'
        f'<p class="sub">{escape(kind.title)} · 시트 {escape(job.sheet)} · {escape(when)} 시작'
        + (f" · 소스 폴더 {escape(job.source_root)}" if job.source_root else "")
        + (f" · 연결 {escape(job.connection)}" if job.connection else "")
        + f"</p>{note}</div>"
        f'<div class="actions"><a class="btn plain" href="/results/{job.id}/summary">'
        "요약 (캡처용)</a>"
        f'<a class="btn plain" href="/results/{job.id}/results.xlsx">엑셀로 내보내기</a>'
        "</div></div>"
    )
    if job.state == "running":
        done, total = _progress(rows)
        percent = round(done * 100 / total) if total else 100
        body += (
            f'<div class="progress"><div style="width:{percent}%"></div></div>'
            f'<p class="sub"><span class="spinner"></span>{done}/{total} 처리 · '
            "5초마다 새로 고칩니다</p>"
            f'<p class="sub">{_row_states(rows)}</p>'
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
    body += _settings_card(job)
    body += _chips(kind, job, rows)
    title = job.title or job.excel_name
    if not rows:
        body += '<div class="card"><p class="empty">분석할 행이 없습니다.</p></div>'
        return page(title, body, refresh=not finished, connection=connection)
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
        '<div class="toolbar"><div class="tabs"><button type="button" data-tab="overview" '
        'class="on">한눈에 보기</button><button type="button" data-tab="detail">'
        "자세히 보기 (diff)</button></div>"
        + _filters(rows)
        + "</div>"
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
            '<button class="btn ghost" type="button" id="toggle-all">수정안 모두 선택</button>'
            '<button class="btn primary" type="submit" id="download">패치 내려받기</button>'
            "</div></form>"
        )
    else:
        body += panels
    return page(title, body, refresh=not finished, connection=connection)


_BOOL_TEXT = {True: "true", False: "false", None: ""}
_SECRET_FIELDS = ("api_key", "project_id")


def _connection_fields(connection: Connection) -> dict[str, str]:
    """수정 양식에 채울 값. 키·프로젝트 ID는 넣지 않는다."""
    return {
        "name": connection.name,
        "client_kind": connection.client_kind,
        "base_url": connection.base_url,
        "model": connection.model,
        "project_header": connection.project_header,
        "api_version": connection.api_version,
        "timeout": f"{connection.timeout:g}",
        "verify_ssl": _BOOL_TEXT[connection.verify_ssl],
        "enable_thinking": _BOOL_TEXT[connection.enable_thinking],
    }


def _connection_row(connection: Connection, is_current: bool, check: str) -> str:
    """연결 한 줄: 이름, 현재 연결 배지, 접속 방식 · 모델 · 주소, 버튼들."""
    name = escape(connection.name)
    meta = " · ".join(
        escape(part) for part in (connection.client_kind, connection.model, connection.base_url)
    )
    badge = " " + _badge("blue", "현재 연결") if is_current else ""
    buttons = [
        '<form method="post" action="/settings/check">'
        f'<input type="hidden" name="name" value="{name}">'
        '<button class="btn plain" type="submit">연결 확인</button></form>'
    ]
    if not is_current:
        buttons.append(
            '<form method="post" action="/settings/select">'
            f'<input type="hidden" name="name" value="{name}">'
            '<button class="btn plain" type="submit">현재 연결로</button></form>'
        )
    if connection.read_only:
        buttons.append('<span class="hint">여기서는 바꿀 수 없음</span>')
    else:
        buttons.append(
            f'<a class="btn plain" href="/settings?edit={quote(connection.name)}#form">수정</a>'
            '<form method="post" action="/settings/delete" '
            "onsubmit=\"return confirm('이 연결을 지울까요?')\">"
            f'<input type="hidden" name="name" value="{name}">'
            '<button class="btn plain" type="submit">삭제</button></form>'
        )
    verdict = "ok" if check == "성공" else "bad"
    checked = f'<p class="sub {verdict}">연결 확인: {escape(check)}</p>' if check else ""
    return (
        f'<div class="card connrow{" current" if is_current else ""}"><div class="what">'
        f'<b>{name}</b>{badge}<p class="sub">{meta}</p>{checked}</div>'
        f'<div class="row-actions">{"".join(buttons)}</div></div>'
    )


def _select(name: str, value: str, options: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<option value="{escape(v)}"{" selected" if v == value else ""}>{escape(label)}</option>'
        for v, label in options
    )
    return f'<select name="{name}">{items}</select>'


def _connection_form(values: Mapping[str, str], editing: str | None, error: str) -> str:
    """추가·수정 양식. 키·프로젝트 ID 칸은 늘 비워 둔다."""

    def text(name: str, label: str, placeholder: str = "", required: bool = False) -> str:
        need = " required" if required else ""
        return (
            f'<label>{label}<input type="text" name="{name}" '
            f'value="{escape(values.get(name, ""))}" placeholder="{escape(placeholder)}"'
            f' autocomplete="off"{need}></label>'
        )

    def secret(name: str, label: str) -> str:
        # 저장된 값은 화면에 내보내지 않는다. 수정 때 비워 두면 저장된 값을 쓴다
        hint = (
            "비워 두면 저장된 값을 씁니다 (주소나 접속 방식을 바꾸면 다시 입력)" if editing else ""
        )
        return (
            f'<label>{label}<input type="password" name="{name}" value="" '
            f'placeholder="{hint}" autocomplete="new-password"></label>'
        )

    kinds = [(kind, kind) for kind in CLIENT_KINDS]
    yes_no = [("true", "true"), ("false", "false")]
    thinking = [("", "서버 기본값"), *yes_no]
    original = f'<input type="hidden" name="original" value="{escape(editing)}">' if editing else ""
    title = "연결 수정" if editing else "연결 추가"
    note = '<p class="hint">키·프로젝트 ID를 비워 두면 저장된 값을 씁니다.</p>' if editing else ""
    cancel = '<a class="btn plain" href="/settings">취소</a>' if editing else ""
    return (
        '<form id="form" class="card" method="post" action="/settings/save">'
        f'<h2 style="margin-top:4px">{title}</h2>{note}'
        + (f'<div class="error">{escape(error)}</div>' if error else "")
        + original
        + '<div class="fields">'
        + text("name", "연결 이름", "예: 사내 게이트웨이", required=True)
        + f"<label>접속 방식{_select('client_kind', values.get('client_kind', ''), kinds)}</label>"
        + text("base_url", "접속 주소 (LLM_BASE_URL)", "https://…/v1", required=True)
        + text("model", "모델 (LLM_MODEL)", required=True)
        + secret("api_key", "API 키 (LLM_API_KEY)")
        + secret("project_id", "프로젝트 ID (LLM_PROJECT_ID)")
        + text("project_header", "프로젝트 헤더 이름 (LLM_PROJECT_HEADER)", "OpenAI-Project")
        + text("api_version", "API 버전 (LLM_API_VERSION, azure)")
        + text("timeout", "응답 대기 초 (LLM_TIMEOUT)", "60")
        + "<label>인증서 확인 (LLM_VERIFY_SSL)"
        + _select("verify_ssl", values.get("verify_ssl", "true"), yes_no)
        + "</label><label>생각 모드 (LLM_ENABLE_THINKING)"
        + _select("enable_thinking", values.get("enable_thinking", ""), thinking)
        + "</label></div>"
        f'<div class="actions"><button class="btn primary" type="submit">저장</button>{cancel}'
        "</div></form>"
    )


def settings_page(
    connections: list[Connection],
    current: str | None,
    message: str = "",
    form: Mapping[str, str] | None = None,
    editing: str | None = None,
    checks: Mapping[str, str] | None = None,
    error: str = "",
    *,
    connection: str | None = None,
) -> str:
    """연결 목록과 추가·수정 양식.

    form은 다시 그릴 양식 값(키·프로젝트 ID는 쓰지 않는다). editing이면 그 연결의 수정 양식이고,
    form이 없으면 그 연결의 값을 채운다. error는 form이 있으면 양식 안에, 없으면 위에 보인다.
    """
    checks = checks or {}
    rows = "".join(
        _connection_row(item, item.name == current, checks.get(item.name, ""))
        for item in connections
    ) or (
        '<div class="card"><p class="empty">아직 연결이 없습니다. '
        "아래 양식으로 추가하세요.</p></div>"
    )
    if form is None and editing:
        target = next((item for item in connections if item.name == editing), None)
        values = _connection_fields(target) if target else {}
    else:
        values = {k: v for k, v in (form or {}).items() if k not in _SECRET_FIELDS}
    top = f'<div class="notice">{escape(error)}</div>' if error and form is None else ""
    if message:
        top += f'<div class="notice paused">{escape(message)}</div>'
    body = (
        '<div class="pagehead"><div><h1>LLM 연결</h1>'
        '<p class="sub">분석을 시작할 때 현재 연결로 LLM에 묻습니다. 작업은 시작할 때의 연결을 '
        "기억합니다. 키와 프로젝트 ID는 화면에 보이지 않습니다.</p></div>"
        '<div class="actions"><a class="btn primary" href="/settings#form">연결 추가</a></div>'
        "</div>" + top + rows + _connection_form(values, editing, error if form is not None else "")
    )
    return page("LLM 연결", body, connection=connection)


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
            ("수정안 모두 선택이 고르는 수정안", str(len(picked))),
            ("같은 줄 겹침으로 다음 차례", str(len(conflicts) - len(picked) - len(manual))),
            ("직접 골라야 하는 수정", str(len(manual))),
            ("다른 파일 수정", str(sum(_edits_other_files(r) for r in fixes))),
        ]
    fact_rows = "".join(
        f'<tr><td>{escape(name)}</td><td class="num">{escape(value)}</td></tr>'
        for name, value in facts
    )
    summary_title = f"작업 요약 · {kind.title} · {job.id[:8]}"
    body = (
        f'<div class="summary-page"><h1>{escape(summary_title)}</h1>'
        '<p class="sub">숫자와 표준 용어만 보입니다(엑셀·파일·함수 이름, 경로, 코드, 이유 없음). '
        f'이 화면은 그대로 캡처해 보내도 됩니다. <a href="/results/{job.id}">결과 화면으로</a></p>'
        '<div class="grid2">'
        + _count_table("대상별 판단", by_label, keys)
        + f'<div class="box"><h3>작업</h3><table>{fact_rows}</table></div></div>'
        + _count_table("검사 종류별", dict(checks), keys)
        + "</div>"
    )
    return page(summary_title, body, refresh=not finished)
