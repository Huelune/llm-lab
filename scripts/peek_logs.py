"""llm-fix-server 대화 기록(private/llm-logs)에서 작업 하나의 판단과 "직접 수정" 행을 센다.

    uv run python scripts/peek_logs.py [파일 이름 패턴] [--job 작업 번호] [--profile 이름]

--job을 빼면 가장 최근 작업을 본다. 패턴(예: rte)을 주면 고칠 수 있게 보낸 정의 가운데 파일
이름에 그 글자가 든 것을 따로 센다(대소문자 무시).

화면은 캡처해서 회사 밖(Claude)으로 보낸다. 그래서 개수와 정해 둔 글만 찍는다. 파일·함수 이름, 경로,
코드, LLM이 쓴 이유, 패턴 글자, 오류 원문은 찍지 않는다. 임시 파일도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

LOG_NAME = re.compile(r"_job(\d+)_row\d+(?:-\d+)?\.md$")
FENCE = re.compile(r"^(`{3,})[^\n`]*\n(.*?)\n\1$", re.S | re.M)
BLOCK_HEAD = re.compile(r"^```c\n// (editable caller: |editable: |reference only: )?(\S+) ", re.M)
MAX_DEFINITIONS = 12  # fixer가 요청 하나에 붙이는 정의 수 상한 (csource.MAX_DEFINITIONS)
DECISIONS = (("fix", "수정"), ("no_fix", "수정 불필요"), ("manual", "직접 수정"))


def newest_job(folder: Path) -> int | None:
    jobs = [int(m.group(1)) for path in folder.glob("*.md") if (m := LOG_NAME.search(path.name))]
    return max(jobs, default=None)


def _first_fence(text: str) -> str | None:
    match = FENCE.search(text)
    return match.group(2) if match else None


def read_logs(folder: Path, job: int) -> list[tuple[str, dict]]:
    """작업의 묶음마다 (마지막으로 보낸 사용자 메시지, 받은 답 JSON). 못 읽은 기록은 뺀다."""
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
            found.append((user, answer))
    return found


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


def manual_counts(
    logs: list[tuple[str, dict]], pattern: str | None = None
) -> list[tuple[str, int]]:
    """직접 수정 행마다, 그 묶음에 무엇이 붙어 갔는지 센다. 한 행이 여러 칸에 들 수 있다."""
    counts: Counter[str] = Counter()
    for user, answer in logs:
        manual = sum(1 for decision in _decisions(answer) if decision == "manual")
        if not manual:
            continue
        blocks = _blocks(user)
        kinds = {kind for kind, _ in blocks}
        if "editable" in kinds:
            counts["editable"] += manual
        if any(kind == "editable" and _matches(file, pattern) for kind, file in blocks):
            counts["editable_pattern"] += manual
        if "caller" in kinds:
            counts["caller"] += manual
        if kinds == {"reference"}:
            counts["reference_only"] += manual
        if not blocks:
            counts["none"] += manual
        if "more lines, not editable" in user:
            counts["clipped"] += manual
        if sum(1 for kind, _ in blocks if kind != "caller") >= MAX_DEFINITIONS:
            counts["capped"] += manual
    keys = ("editable", "editable_pattern", "caller", "reference_only", "none", "clipped", "capped")
    return [(key, counts[key]) for key in keys]


def other_file_fixes(logs: list[tuple[str, dict]], pattern: str | None = None) -> tuple[int, int]:
    """다른 파일을 고친 수정 행 수, 그중 패턴에 맞는 파일을 고친 행 수."""
    rows = matched = 0
    for _, answer in logs:
        edits = answer.get("edits") if isinstance(answer.get("edits"), list) else []
        files = [e.get("file") for e in edits if isinstance(e, dict) and e.get("file")]
        fixes = sum(1 for decision in _decisions(answer) if decision == "fix")
        if files and fixes:
            rows += fixes
            if any(_matches(file, pattern) for file in files):
                matched += fixes
    return rows, matched


LABELS = {
    "editable": "고칠 수 있는 정의가 붙음",
    "editable_pattern": "  그중 패턴에 맞는 파일의 정의",
    "caller": "고칠 수 있는 호출자가 붙음",
    "reference_only": "참고만 하는 정의만 붙음",
    "none": "붙은 정의·호출자 없음",
    "clipped": "80줄에서 잘린 정의가 있음",
    "capped": "정의가 상한(12개)까지 붙음",
}


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


def summarize(
    folder: Path, job: int | None = None, pattern: str | None = None, db: Path | None = None
) -> list[str]:
    if job is None:
        # 작업 DB의 최근 작업을 먼저 본다. 기록 파일만 보면 기록이 없는 최근 작업을 건너뛴다
        job = newest_db_job(db) or (newest_job(folder) if folder.is_dir() else None)
    if job is None:
        return ["대화 기록이 없습니다. 서버를 켠 프로젝트 폴더에서 실행하세요."]
    logs = read_logs(folder, job) if folder.is_dir() else []
    if not logs:
        return [
            f"작업 {job}의 대화 기록이 없습니다.",
            "아직 처리 중이거나, LLM을 부르기 전에 끝났거나, 서버를 --profile로 켰으면 같은 "
            "--profile을 붙이세요.",
        ]
    decided = Counter(decision for _, answer in logs for decision in _decisions(answer))
    lines = [
        f"작업 {job}: 묶음 {len(logs)}개 (마지막 요청 기준)",
        "판단 행: " + " / ".join(f"{label} {decided[key]}" for key, label in DECISIONS),
        "",
        "직접 수정 행이 받은 것 (한 행이 여러 줄에 들 수 있음):",
    ]
    for key, count in manual_counts(logs, pattern):
        if key == "editable_pattern" and not pattern:
            continue
        lines.append(f"  {LABELS[key]:<28}{count:>6}")
    rows, matched = other_file_fixes(logs, pattern)
    lines += ["", f"다른 파일을 고친 수정 행: {rows}"]
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
        screen = summarize(folder, args.job, args.pattern, data / "fixer.db")
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
