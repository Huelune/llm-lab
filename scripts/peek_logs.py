"""llm-fix-server 대화 기록(private/llm-logs)에서 작업 하나의 판단과 "직접 수정" 행을 센다.
되묻기(LLM이 정의를 더 달라고 한 것)도 센다.

    uv run python scripts/peek_logs.py [파일 이름 패턴] [--job 작업 번호] [--profile 이름]

--job을 빼면 가장 최근 작업을 본다. 패턴(예: rte)을 주면 다른 파일을 고친 수정 행 가운데 파일
이름에 그 글자가 든 것을 따로 센다(대소문자 무시).

화면은 캡처해서 회사 밖(Claude)으로 보낸다. 그래서 개수와 정해 둔 글만 찍는다. 파일·함수 이름, 경로,
코드, LLM이 쓴 이유, 패턴 글자, 오류 원문은 찍지 않는다. 직접 수정 이유의 원문과 이름이 나오는
자리는 임시 폴더의 peek_logs_jobN.txt에만 쓴다. 그 파일은 회사 PC에서만 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from llm_lab.csource import MAX_DEFINITIONS, SOURCE_SUFFIXES, Definition, identifiers
from llm_lab.followups import (
    AGAIN_LINE,
    LAST_LINE,
    LATER_LINE,
    MISSING_LINE,
    SHOWN_LINE,
    UNAVAILABLE_LINE,
    need_names,
)
from llm_lab.sources import DecodeError, decode_source
from llm_lab.store import Job, Store

LOG_NAME = re.compile(r"_job(\d+)_row\d+(?:-\d+)?\.md$")
FENCE = re.compile(r"^(`{3,})[^\n`]*\n(.*?)\n\1$", re.S | re.M)
BLOCK_HEAD = re.compile(r"^```c\n// (editable caller: |editable: |reference only: )?(\S+) ", re.M)
CODE_BLOCK = re.compile(r"^```c\n(.*?)\n```$", re.S | re.M)
CODE_RANGE = re.compile(r"^Code \(lines (\d+)-(\d+) of (.+?)[;)]", re.M)
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # 한글이 붙은 이름(예: 이름을)에서도 이름만
SHOWN_CALLS = 2  # 화면에 모양을 찍는 호출 이름 수 (나머지는 자세한 목록에)
DECISIONS = (("fix", "수정"), ("no_fix", "수정 불필요"), ("manual", "직접 수정"))


def newest_job(folder: Path) -> int | None:
    jobs = [int(m.group(1)) for path in folder.glob("*.md") if (m := LOG_NAME.search(path.name))]
    return max(jobs, default=None)


def _first_fence(text: str) -> str | None:
    match = FENCE.search(text)
    return match.group(2) if match else None


def read_logs(folder: Path, job: int) -> list[tuple[str, dict]]:
    """작업의 묶음마다 (마지막으로 보낸 사용자 메시지, 받은 답 JSON). 못 읽은 기록은 뺀다."""
    return [(user, answer) for _, user, answer in read_log_files(folder, job)]


def read_log_files(folder: Path, job: int) -> list[tuple[Path, str, dict]]:
    """read_logs와 같고, 기록 파일 경로를 앞에 붙인다."""
    found = []
    for path in sorted(folder.glob(f"*_job{job}_row*.md")):
        if not LOG_NAME.search(path.name):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        last = text.split("\n## 요청 ")[-1]
        sent_at = last.find("### 보낸 내용: user")
        got_at = last.find("### 받은 내용")
        if sent_at < 0 or got_at < 0:
            continue
        user = _first_fence(last[sent_at:got_at])
        content = _first_fence(last[got_at:])
        try:
            answer = json.loads(content or "")
        except json.JSONDecodeError:
            continue
        if user is not None and isinstance(answer, dict):
            found.append((path, user, answer))
    return found


REUSED = "이전 결과 재사용 - LLM을 부르지 않음"  # worker가 저장된 답을 다시 쓸 때 남기는 결과 줄

TALK_HEAD = re.compile(r"^### (?:보낸 내용: (\w+)|받은 내용)[^\n]*\n", re.M)


def _conversation(text: str) -> list[tuple[str, str]]:
    """기록 파일의 마지막 요청에서 (역할, 내용) 차례. 받은 답은 assistant로 끝에 붙는다."""
    last = text.split("\n## 요청 ")[-1]
    found = []
    for match in TALK_HEAD.finditer(last):
        content = _first_fence(last[match.end() :])
        if content is not None:
            found.append((match.group(1) or "assistant", content))
    return found


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _names_on(message: str, prefix: str) -> list[str]:
    """되묻기 메시지에서 prefix로 시작하는 줄의 이름들."""
    for line in message.splitlines():
        if line.startswith(prefix):
            return [name for name in line[len(prefix) :].rstrip(".").split(", ") if name]
    return []


def _listed(message: str, prefix: str) -> int:
    return len(_names_on(message, prefix))


def _follow_ups(talk: list[tuple[str, str]]) -> list[tuple[dict, str]]:
    """대화의 되묻기마다 (그 앞의 임시 답 JSON, 되묻기 메시지)."""
    return [
        (_json(talk[index - 1][1]) if index else {}, content)
        for index, (role, content) in enumerate(talk)
        if role == "user" and (AGAIN_LINE in content or LAST_LINE in content)
    ]


def _received(talk: list[tuple[str, str]]) -> set[str]:
    """되물어 받은 이름: need 가운데 프로젝트에 없음·상한으로 못 보냄·찾을 수 없음이 아닌 것."""
    got: set[str] = set()
    for previous, message in _follow_ups(talk):
        if UNAVAILABLE_LINE in message:
            continue
        refused = {*_names_on(message, MISSING_LINE), *_names_on(message, LATER_LINE)}
        got.update(name for name in need_names(previous) if name not in refused)
    return got


def received_names(folder: Path, job: int) -> list[set[str]]:
    """read_logs의 묶음마다 되물어 받은 이름."""
    return [
        _received(_conversation(path.read_text(encoding="utf-8", errors="replace")))
        for path, _, _ in read_log_files(folder, job)
    ]


def _decided(answer: dict) -> dict[object, object]:
    items = answer.get("findings") if isinstance(answer.get("findings"), list) else []
    return {item.get("number"): item.get("decision") for item in items if isinstance(item, dict)}


def follow_up_counts(folder: Path, job: int) -> dict[str, int]:
    """작업의 되묻기를 센다: 되물은 묶음, 횟수별 묶음, 요청한 이름의 처리, 판단이 바뀐 행.

    화면의 묶음 수와 같은 기록(read_log_files가 읽은 것)만 센다.
    """
    counts: Counter[str] = Counter()
    for path, _, _ in read_log_files(folder, job):
        talk = _conversation(path.read_text(encoding="utf-8", errors="replace"))
        asked = _follow_ups(talk)
        rounds = len(asked)
        first_answer = asked[0][0] if asked else None
        for previous, content in asked:
            names = len(need_names(previous))
            counts["asked"] += names
            counts["missing"] += _listed(content, MISSING_LINE)
            counts["shown"] += _listed(content, SHOWN_LINE)
            counts["later"] += _listed(content, LATER_LINE)
            if UNAVAILABLE_LINE in content:
                counts["unavailable"] += names
        if not rounds:
            continue
        counts["groups"] += 1
        counts[f"round_{min(rounds, 3)}"] += 1
        before = _decided(first_answer or {})
        after = _decided(_json(talk[-1][1]))
        counts["changed"] += sum(
            1 for number, decision in after.items() if before.get(number) != decision
        )
    counts["sent"] = max(
        0,
        counts["asked"]
        - counts["missing"]
        - counts["shown"]
        - counts["later"]
        - counts["unavailable"],
    )
    keys = ("groups", "round_1", "round_2", "round_3", "asked", "sent", "missing", "shown")
    keys += ("later", "unavailable", "changed")
    return {key: counts[key] for key in keys}


def reused_count(folder: Path, job: int) -> int:
    """저장된 답을 다시 써서 LLM을 부르지 않은 묶음의 기록 수 (보낸 내용이 없다)."""
    return sum(
        1
        for path in folder.glob(f"*_job{job}_row*.md")
        if LOG_NAME.search(path.name)
        and REUSED in path.read_text(encoding="utf-8", errors="replace")
    )


def _decisions(answer: dict) -> list[str]:
    items = answer.get("findings")
    return (
        [item.get("decision", "") for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )


def _blocks(user: str) -> list[tuple[str, str]]:
    """사용자 메시지의 정의·호출자 블록: (종류, 파일). 종류는 editable / caller / reference."""
    labels = {"editable caller: ": "caller", "editable: ": "editable"}
    return [
        (labels.get(m.group(1) or "", "reference"), m.group(2)) for m in BLOCK_HEAD.finditer(user)
    ]


def _matches(file: str, pattern: str | None) -> bool:
    return bool(pattern) and pattern.casefold() in file.rsplit("/", 1)[-1].casefold()


def _loose(name: str) -> str:
    """fixer와 같은 파일 이름 비교: 역슬래시는 /로, 앞의 ./은 떼고, 대소문자는 무시."""
    name = name.replace("\\", "/")
    return (name[2:] if name.startswith("./") else name).lower()


def _other_files(user: str, answer: dict) -> set[str]:
    """답의 수정이 고친 다른 파일. file이 비었거나 검사 파일 자신을 적은 수정은 뺀다.

    서버(fixer)도 검사 파일 경로를 적은 수정은 검사 파일 수정으로 받는다.
    """
    shown = CODE_RANGE.search(user)
    own = _loose(shown.group(3)) if shown else None
    edits = answer.get("edits") if isinstance(answer.get("edits"), list) else []
    return {
        e["file"]
        for e in edits
        if isinstance(e, dict)
        and isinstance(e.get("file"), str)
        and e["file"]
        and _loose(e["file"]) != own
    }


def other_file_fixes(logs: list[tuple[str, dict]], pattern: str | None = None) -> tuple[int, int]:
    """다른 파일을 고친 수정 행 수, 그중 패턴에 맞는 파일을 고친 행 수."""
    rows = matched = 0
    for user, answer in logs:
        files = _other_files(user, answer)
        fixes = sum(1 for decision in _decisions(answer) if decision == "fix")
        if files and fixes:
            rows += fixes
            if any(_matches(file, pattern) for file in files):
                matched += fixes
    return rows, matched


EDITABLE_HEAD = re.compile(r"^// (editable caller: |editable: )(\S+) ", re.M)


def other_file_places(folder: Path, job: int) -> dict[str, int]:
    """다른 파일을 고친 수정 행을, 고친 파일이 어떻게 보내졌는지로 센다 (한 행이 여러 칸에).

    caller: 호출자 조각 / definition: 첫 요청의 정의 / asked: 되물어 받은 정의 / other: 그 밖.
    header는 고친 파일이 .h인 행이다.
    """
    counts: Counter[str] = Counter()
    for path, user, answer in read_log_files(folder, job):
        fixes = sum(1 for decision in _decisions(answer) if decision == "fix")
        files = _other_files(user, answer)
        if not fixes or not files:
            continue
        first = {m.group(2): m.group(1) for m in EDITABLE_HEAD.finditer(user)}
        talk = _conversation(path.read_text(encoding="utf-8", errors="replace"))
        later = {
            m.group(2) for _, message in _follow_ups(talk) for m in EDITABLE_HEAD.finditer(message)
        }
        places = set()
        for file in files:
            if first.get(file) == "editable caller: ":
                places.add("caller")
            elif file in first:
                places.add("definition")
            elif file in later:
                places.add("asked")
            else:
                places.add("other")
        for place in places:
            counts[place] += fixes
        counts["header"] += fixes if any(f.casefold().endswith(".h") for f in files) else 0
    return {key: counts[key] for key in ("caller", "definition", "asked", "header", "other")}


def _where(definition: Definition) -> str:
    if definition.first == definition.last:
        return f"line {definition.first}"
    return f"lines {definition.first}-{definition.last}"


def _sent(user: str, shown: re.Match[str] | None, definition: Definition) -> bool:
    """정의가 요청에 실렸는지: 보낸 코드 범위 안이거나, 같은 파일·줄의 정의 블록이 있다."""
    if shown and definition.file == shown.group(3):
        if int(shown.group(1)) <= definition.first <= definition.last <= int(shown.group(2)):
            return True
    return f" {definition.file} {_where(definition)}\n" in user


@dataclass
class Reason:
    """직접 수정 판단 하나: 칸들과, 정의를 못 찾은 호출 이름."""

    group: int  # logs 안의 순서
    number: object  # 묶음 안 판단 번호
    checked: str  # 검사 파일 (보낸 코드의 파일 이름)
    text: str
    kinds: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)


def manual_reasons(
    logs: list[tuple[str, dict]],
    definitions: list[Definition],
    received: list[set[str]] | None = None,
) -> list[Reason]:
    """직접 수정 판단마다 이유가 짚은 코드 이름을 나눈다. 한 판단이 여러 칸에 들 수 있다.

    received는 묶음마다 되물어 받은 이름(received_names)이다. 그 이름은 요청에 있었던 것으로 본다.

    cut: 작업에 정의가 저장됐지만 요청에 안 붙은 이름 / missing_call: 보낸 코드에서 불렀지만 저장된
    정의가 없는 이름 / missing_other: 그 밖에 정의가 없는 이름(지역 변수·매개변수·멤버 등) /
    sent: 짚은 이름이 모두 요청에 있었다 / none: 코드 이름을 짚지 않았다.
    """
    stored: dict[str, list[Definition]] = {}
    for definition in definitions:
        if definition.kind not in ("caller", "caller_ref"):
            stored.setdefault(definition.name, []).append(definition)
    found: list[Reason] = []
    for group, (user, answer) in enumerate(logs):
        items = answer.get("findings") if isinstance(answer.get("findings"), list) else []
        code_text = "\n".join(CODE_BLOCK.findall(user))
        code = set(identifiers(code_text))
        shown = CODE_RANGE.search(user)
        sent = partial(_sent, user, shown)
        got = received[group] if received else set()
        for item in items:
            if not isinstance(item, dict) or item.get("decision") != "manual":
                continue
            text = item.get("reason") if isinstance(item.get("reason"), str) else ""
            reason = Reason(group, item.get("number"), shown.group(3) if shown else "", text)
            found.append(reason)
            named = {
                name for name in WORD.findall(text) if name in stored or name in code or name in got
            }
            if not named:
                reason.kinds.add("none")
                continue
            unsent = {
                n for n in named if n in stored and n not in got and not any(map(sent, stored[n]))
            }
            missing = {name for name in named if name not in stored and name not in got}
            reason.calls = {
                name for name in missing if re.search(rf"\b{re.escape(name)}\s*\(", code_text)
            }
            if unsent:
                reason.kinds.add("cut")
            if reason.calls:
                reason.kinds.add("missing_call")
            if missing - reason.calls:
                reason.kinds.add("missing_other")
            if not unsent and not missing:
                reason.kinds.add("sent")
    return found


REASON_LABELS = {
    "cut": "저장됐지만 요청에 안 붙은 정의",
    "missing_call": "정의를 못 찾은 호출 함수",
    "missing_other": "그 밖의 못 찾은 이름(변수 등)",
    "sent": "짚은 이름이 모두 요청에 있었음",
    "none": "코드 이름을 짚지 않음",
}


def reason_counts(
    logs: list[tuple[str, dict]],
    definitions: list[Definition],
    received: list[set[str]] | None = None,
) -> list[tuple[str, int]]:
    reasons = manual_reasons(logs, definitions, received)
    counts = Counter(kind for reason in reasons for kind in reason.kinds)
    return [(key, counts[key]) for key in REASON_LABELS]


def project_texts(root: Path) -> dict[str, str]:
    """root 아래 읽히는 .c·.h의 원문 (소스 폴더 기준 상대 경로 → 글). 점 폴더는 건너뛴다."""
    texts: dict[str, str] = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if Path(name).suffix.casefold() not in SOURCE_SUFFIXES:
                continue
            path = Path(folder, name)
            try:
                source = decode_source(name, path.read_bytes())
            except DecodeError, OSError:
                continue
            texts[path.relative_to(root).as_posix()] = source.text
    return texts


SHAPES = {
    "macro": "매크로",
    "pasted": "이어 붙임(##)",
    "declaration": "선언",
    "definition": "정의 모양",
    "call": "호출",
    "comment": "주석",
    "other": "그 밖",
}
TYPE_WORDS = re.compile(
    r"^[A-Za-z_][\w\s\*,()]*$"
)  # 이름 앞이 타입·속성 낱말뿐인지 (예: static int *)


def shape(line: str, name: str) -> str:
    """이름이 나오는 줄의 모양. 스캐너가 정의를 왜 못 알아봤는지 보려고 센다."""
    text = line.strip()
    if text.startswith("#"):
        return "pasted" if "##" in text else "macro"
    if text.startswith(("//", "/*", "*")):
        return "comment"
    at = re.search(rf"\b{re.escape(name)}\s*\(", text)
    if not at:
        return "other"
    before = text[: at.start()].strip()
    typed = bool(before) and bool(TYPE_WORDS.match(before)) and not before.startswith("return")
    if typed and "=" not in before:
        return "declaration" if text.endswith(";") else "definition"
    return "call"


def places(name: str, texts: dict[str, str], limit: int = 200) -> list[tuple[str, int, str]]:
    """이름이 글자로 나오는 자리 (파일, 줄 번호, 원문), limit개까지."""
    word = re.compile(rf"\b{re.escape(name)}\b")
    found = []
    for file, text in texts.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if word.search(line):
                found.append((file, number, line))
                if len(found) >= limit:
                    return found
    return found


@dataclass
class Call:
    """정의를 못 찾은 호출 이름 하나: 몇 판단이 짚었는지, 프로젝트에 나오는 자리와 모양."""

    name: str
    rows: int
    checked: set[str]
    spots: list[tuple[str, int, str]]

    def other_files(self) -> int:
        return sum(1 for file, _, _ in self.spots if file not in self.checked)

    def shapes(self) -> Counter[str]:
        return Counter(shape(line, self.name) for _, _, line in self.spots)


def unfound_calls(reasons: list[Reason], texts: dict[str, str]) -> list[Call]:
    """정의를 못 찾은 호출 이름마다 Call. 많이 짚힌 것부터."""
    rows: Counter[str] = Counter()
    checked: dict[str, set[str]] = {}
    for reason in reasons:
        for call in reason.calls:
            rows[call] += 1
            checked.setdefault(call, set()).add(reason.checked)
    calls = [Call(name, rows[name], checked[name], places(name, texts)) for name in rows]
    return sorted(calls, key=lambda c: (-c.rows, -len(c.spots), c.name))


def write_detail(
    path: Path,
    job: int,
    names: list[str],
    reasons: list[Reason],
    calls: list[Call] | None,
) -> None:
    """직접 수정 판단의 이유 원문과, 정의를 못 찾은 호출 이름이 나오는 자리. 회사 PC에서만 본다."""
    lines = [
        f"작업 {job} 직접 수정 판단 {len(reasons)}개 - 회사 PC에서만 보고 밖으로 보내지 않습니다.",
        "",
    ]
    for number, call in enumerate(calls or [], start=1):
        lines.append(f"== 호출 이름 {number}: {call.name} (직접 수정 {call.rows}행)")
        for file, line_number, line in call.spots[:20]:
            mark = "검사 파일" if file in call.checked else SHAPES[shape(line, call.name)]
            lines.append(f"  [{mark}] {file}:{line_number}: {line.strip()[:160]}")
        if not call.spots:
            lines.append("  프로젝트 어디에도 없음")
        lines.append("")
    for key, label in REASON_LABELS.items():
        chosen = [reason for reason in reasons if key in reason.kinds]
        if not chosen:
            continue
        lines += [f"== {label} ({len(chosen)}개)", ""]
        for reason in chosen:
            lines.append(f"  {names[reason.group]} 판단 {reason.number}")
            lines.append(f"    이유: {reason.text}")
            lines += [f"    정의를 못 찾은 호출: {call}" for call in sorted(reason.calls)]
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8-sig")


# Polyspace Code Prover 런타임 검사 이름 (공개 문서의 표준 용어). 화면에는 이 이름만 찍는다
CHECKS = (
    "Absolute address usage",
    "Correctness condition",
    "Division by zero",
    "Function not called",
    "Function not reachable",
    "Function not returning value",
    "Illegally dereferenced pointer",
    "Invalid shift operations",
    "Invalid use of standard library routine",
    "Invalid use of standard library floating point routine",
    "Invalid use of standard library integer routine",
    "Invalid use of standard library memory routine",
    "Invalid use of standard library string routine",
    "Non-initialized local variable",
    "Non-initialized pointer",
    "Non-initialized variable",
    "Non-terminating call",
    "Non-terminating loop",
    "Out of bounds array index",
    "Overflow",
    "Return value not initialized",
    "Subnormal float",
    "Uncaught exception",
    "Unreachable code",
    "User assertion",
)
KNOWN_CHECKS = {name.casefold(): name for name in CHECKS}
FINDING_BLOCK = re.compile(r"^Finding (\d+):\n((?:- .*\n?)+)", re.M)
SHOWN_CHECKS = 5  # 화면에 찍는 검사 종류 수


def _finding_facts(user: str) -> dict[str, dict[str, str]]:
    """사용자 메시지의 Finding 번호 → {사실 이름(소문자): 값}."""
    found = {}
    for match in FINDING_BLOCK.finditer(user):
        facts = {}
        for line in match.group(2).splitlines():
            name, _, value = line[2:].partition(": ")
            facts[name.strip().casefold()] = value.strip()
        found[match.group(1)] = facts
    return found


def manual_checks(logs: list[tuple[str, dict]]) -> list[tuple[str, int]]:
    """직접 수정 행의 (색 검사 이름, 행 수), 많은 것부터. 표준 이름이 아니면 "그 밖"으로 묶는다."""
    counts: Counter[str] = Counter()
    for user, answer in logs:
        facts = _finding_facts(user)
        items = answer.get("findings") if isinstance(answer.get("findings"), list) else []
        for item in items:
            if not isinstance(item, dict) or item.get("decision") != "manual":
                continue
            fact = facts.get(str(item.get("number")), {})
            check = KNOWN_CHECKS.get(fact.get("check", "").casefold())
            color = fact.get("type", "").split(" ")[0]
            if check is None or color not in ("Red", "Orange"):
                counts["그 밖"] += 1
            else:
                counts[f"{color} {check}"] += 1
    other = counts.pop("그 밖", 0)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked + ([("그 밖", other)] if other else [])


def newest_db_job(db: Path | None) -> int | None:
    """작업 DB의 가장 최근 작업 번호. DB가 없거나 못 읽으면 None."""
    if db is None or not db.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
            found = connection.execute("SELECT MAX(id) FROM jobs").fetchone()
    except sqlite3.Error:
        return None
    return found[0] if found and found[0] is not None else None


def _reason_lines(
    folder: Path,
    job: int,
    logs: list[tuple[str, dict]],
    stored: Job,
    store: Store,
    detail: Path | None,
) -> list[str]:
    reasons = manual_reasons(logs, store.definitions(job), received_names(folder, job))
    counts = Counter(kind for reason in reasons for kind in reason.kinds)
    lines = [f"  {REASON_LABELS[key]:<28}{counts[key]:>6}" for key in REASON_LABELS]
    root = Path(stored.source_root) if stored.source_root else None
    texts = project_texts(root) if root and root.is_dir() else None
    calls = unfound_calls(reasons, texts) if texts is not None else None
    if calls is None:
        lines.append("소스 폴더를 찾지 못해 호출 이름을 프로젝트에서 찾지 않았습니다.")
    else:
        elsewhere = sum(1 for call in calls if call.other_files())
        lines.append(
            f"정의를 못 찾은 호출 이름 {len(calls)}개: 다른 파일에 글자가 있음 {elsewhere}개, "
            f"프로젝트 어디에도 없음 {len(calls) - elsewhere}개"
        )
        for number, call in enumerate(calls[:SHOWN_CALLS], start=1):
            shapes = call.shapes()
            lines.append(
                f"  호출 이름 {number}: 직접 수정 {call.rows}행, 다른 파일 {call.other_files()}곳, "
                f"검사 파일 {len(call.spots) - call.other_files()}곳"
            )
            lines.append(
                "    모양: " + ", ".join(f"{label} {shapes[key]}" for key, label in SHAPES.items())
            )
        if len(calls) > SHOWN_CALLS:
            lines.append(f"  나머지 {len(calls) - SHOWN_CALLS}개는 자세한 목록에 있습니다.")
    if detail is not None and reasons:
        name = f"peek_logs_job{job}.txt"
        names = [path.name for path, _, _ in read_log_files(folder, job)]
        write_detail(detail / name, job, names, reasons, calls)
        lines.append(f"자세한 목록(회사 PC에서만 보기): %TEMP%\\{name}")
    return lines


def summarize(
    folder: Path,
    job: int | None = None,
    pattern: str | None = None,
    db: Path | None = None,
    detail: Path | None = None,
) -> list[str]:
    if job is None:
        # 작업 DB의 최근 작업을 먼저 본다. 기록 파일만 보면 기록이 없는 최근 작업을 건너뛴다
        job = newest_db_job(db) or (newest_job(folder) if folder.is_dir() else None)
    if job is None:
        return ["대화 기록이 없습니다. 서버를 켠 프로젝트 폴더에서 실행하세요."]
    logs = read_logs(folder, job) if folder.is_dir() else []
    reused = reused_count(folder, job) if folder.is_dir() else 0
    if not logs and reused:
        return [
            f"작업 {job}: 묶음 {reused}개 모두 저장된 답을 다시 썼습니다(LLM을 부르지 않음).",
            "처음 답을 만든 작업 번호를 --job으로 주거나, 저장된 답을 지우고 다시 돌리세요.",
        ]
    if not logs:
        return [
            f"작업 {job}의 대화 기록이 없습니다.",
            "아직 처리 중이거나, LLM을 부르기 전에 끝났거나, 서버를 --profile로 켰으면 같은 "
            "--profile을 붙이세요.",
        ]
    decided = Counter(decision for _, answer in logs for decision in _decisions(answer))
    capped = sum(
        1
        for user, _ in logs
        if sum(1 for kind, _ in _blocks(user) if kind != "caller") >= MAX_DEFINITIONS
    )
    manual_groups = sum(1 for _, answer in logs if "manual" in _decisions(answer))
    follow = follow_up_counts(folder, job)
    lines = [
        f"작업 {job}: 묶음 {len(logs)}개 (마지막 요청 기준), 저장된 답 재사용 묶음 {reused}개",
        "판단 행: " + " / ".join(f"{label} {decided[key]}" for key, label in DECISIONS),
        f"정의가 상한({MAX_DEFINITIONS}개)까지 붙은 묶음: {capped} / {len(logs)}, "
        f"직접 수정이 나온 묶음: {manual_groups}",
        f"되묻기: 되물은 묶음 {follow['groups']} / {len(logs)} "
        f"(1번 {follow['round_1']}, 2번 {follow['round_2']}, 3번 {follow['round_3']})",
        f"  요청한 이름 {follow['asked']}: 보냄 {follow['sent']}, "
        f"프로젝트에 없음 {follow['missing']}, 이미 보냄 {follow['shown']}",
        f"  상한으로 못 보냄 {follow['later']}, 찾을 수 없었음 {follow['unavailable']}",
        f"  되묻기 뒤 판단이 바뀐 행: {follow['changed']}",
    ]
    lines += ["", "직접 수정 이유가 짚은 이름 (한 행이 여러 줄에 들 수 있음):"]
    stored = Store(db).job(job) if db is not None and db.is_file() else None
    if stored is None:
        lines.append(
            "  작업 DB에 이 작업이 없어 세지 않았습니다. 서버와 같은 --profile을 붙이세요."
        )
    else:
        lines += _reason_lines(folder, job, logs, stored, Store(db), detail)
    lines += ["", "직접 수정 행의 검사 종류:"]
    checks = manual_checks(logs)
    lines += [f"  {label:<40}{count:>6}" for label, count in checks[:SHOWN_CHECKS]]
    if len(checks) > SHOWN_CHECKS:
        rest = sum(count for _, count in checks[SHOWN_CHECKS:])
        lines.append(f"  나머지 {len(checks) - SHOWN_CHECKS}종류 {rest}행")
    rows, matched = other_file_fixes(logs, pattern)
    lines += ["", f"다른 파일을 고친 수정 행: {rows}"]
    if rows:
        place = other_file_places(folder, job)
        lines.append(
            f"  고친 곳: 호출자 {place['caller']}, 첫 요청의 정의 {place['definition']}, "
            f"되물어 받은 정의 {place['asked']}, 그 밖 {place['other']} "
            f"/ 헤더(.h) {place['header']}"
        )
    if pattern:
        lines.append(f"  그중 패턴에 맞는 파일을 고친 행: {matched}")
    return lines


def clear_screen() -> None:
    # 고정 명령(cls/clear)만 쓰므로 os.system으로 충분하다
    os.system("cls" if os.name == "nt" else "clear")


def main() -> int:
    parser = argparse.ArgumentParser(prog="peek_logs", description="대화 기록 요약 (개수만)")
    parser.add_argument("pattern", nargs="?", help="파일 이름 패턴 (예: rte)")
    parser.add_argument("--job", type=int, help="작업 번호 (빼면 가장 최근)")
    parser.add_argument("--profile", help="llm-fix-server --profile 이름")
    args = parser.parse_args()
    data = Path("private") / (args.profile or "")
    folder = data / "llm-logs"
    try:
        temp = Path(os.environ.get("TEMP") or tempfile.gettempdir())
        screen = summarize(folder, args.job, args.pattern, data / "fixer.db", temp)
    except Exception as exc:
        clear_screen()
        print(f"요약하지 못했습니다: {type(exc).__name__}")
        print("오류 원문은 경로나 코드가 들어 있을 수 있어 찍지 않습니다.")
        return 1
    clear_screen()
    print("\n".join(screen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
