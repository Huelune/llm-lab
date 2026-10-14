"""llm-fix-server 작업 하나가 정의·호출자를 왜 못 붙였는지 개수로만 센다 (읽기만 한다).

    uv run python scripts/peek_job.py [파일·이름 패턴] [--job 작업 번호] [--profile 이름]

--job을 빼면 가장 최근 작업을 본다. 패턴(예: rte)을 주면 그 글자가 든 파일과 이름을 따로 센다.
작업 DB에 저장된 것을 세고, 묶음마다 요청을 다시 만들어 무엇이 붙는지 센다(LLM은 부르지 않는다).
작업의 소스 폴더가 이 PC에 있으면 다시 훑어 못 읽은 파일을 이유별로 센다.

화면은 캡처해서 회사 밖(Claude)으로 보낸다. 그래서 개수와 정해 둔 글만 찍는다. 파일·함수 이름, 경로,
코드, 패턴 글자, 오류 원문은 찍지 않는다. 임시 파일도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from llm_lab.csource import SOURCE_SUFFIXES, identifiers
from llm_lab.fixer import FixError, make_request, split_lines
from llm_lab.kinds import KINDS
from llm_lab.sources import DecodeError, decode_source
from llm_lab.store import Store


def _has(name: str, pattern: str | None) -> bool:
    return bool(pattern) and pattern.casefold() in name.casefold()


def _rescan(root: Path, pattern: str | None) -> Counter[str]:
    """프로젝트의 .c·.h를 다시 읽어 못 읽은 파일을 이유별로 센다."""
    counts: Counter[str] = Counter()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if Path(name).suffix.casefold() not in SOURCE_SUFFIXES:
                continue
            counts["project_files"] += 1
            matched = _has(name, pattern)
            counts["pattern_files"] += matched
            try:
                decode_source(name, Path(folder, name).read_bytes())
                continue
            except DecodeError as exc:
                reason = "mixed_newlines" if "줄바꿈" in str(exc) else "encoding"
            except OSError:
                reason = "open"
            counts[f"unreadable_{reason}"] += 1
            counts["pattern_unreadable"] += matched
    return counts


def facts(db: Path, job_id: int | None = None, pattern: str | None = None) -> list[tuple[str, int]]:
    store = Store(db)
    job = store.job(job_id) if job_id is not None else next(iter(store.jobs(limit=1)), None)
    if job is None:
        raise LookupError("작업 없음")
    definitions = store.definitions(job.id)
    sources = store.sources(job.id)
    counts: Counter[str] = Counter()
    counts["job"] = job.id
    counts["stored_files"] = len(sources)
    counts["stored_definitions"] = sum(
        1 for d in definitions if d.kind not in ("caller", "caller_ref")
    )
    counts["stored_callers"] = sum(1 for d in definitions if d.kind == "caller")
    defined = {d.name for d in definitions if d.kind not in ("caller", "caller_ref")}
    kind = KINDS.get(job.kind)
    context = json.loads(job.context) if job.context else None
    groups: dict[str, list] = {}
    for row in store.rows(job.id):
        if row.file_name and row.file_name in sources and row.finding.line is not None:
            groups.setdefault(row.grp or f"#{row.id}", []).append(row)
    used: set[str] = set()
    for rows in groups.values():
        counts["groups"] += 1
        source = sources[rows[0].file_name]
        findings = [row.finding for row in rows]
        if kind is None:
            continue
        try:
            request = make_request(kind, findings, source, context, definitions, sources)
        except FixError:
            counts["groups_failed"] += 1
            continue
        counts["groups_with_definitions"] += bool(request.definitions)
        counts["groups_with_callers"] += bool(request.callers)
        sent = "\n".join(split_lines(source.text)[request.first - 1 : request.last])
        used.update(name for name in identifiers(sent) if _has(name, pattern))
    counts["pattern_names_used"] = len(used)
    counts["pattern_names_defined"] = len(used & defined)
    root = Path(job.source_root) if job.source_root else None
    counts["rescanned"] = bool(root and root.is_dir())
    if counts["rescanned"]:
        assert root is not None
        counts.update(_rescan(root, pattern))
    keys = (
        "job",
        "stored_files",
        "stored_definitions",
        "stored_callers",
        "groups",
        "groups_with_definitions",
        "groups_with_callers",
        "groups_failed",
        "pattern_names_used",
        "pattern_names_defined",
        "rescanned",
        "project_files",
        "unreadable_mixed_newlines",
        "unreadable_encoding",
        "unreadable_open",
        "pattern_files",
        "pattern_unreadable",
    )
    return [(key, counts[key]) for key in keys]


def summarize(db: Path, job_id: int | None = None, pattern: str | None = None) -> list[str]:
    if not db.is_file():
        return ["작업 DB가 없습니다. 서버를 켠 프로젝트 폴더에서 실행하세요."]
    try:
        f = dict(facts(db, job_id, pattern))
    except LookupError:
        return ["작업이 없습니다. 작업 번호를 확인하세요."]
    lines = [
        f"작업 {f['job']}: 저장된 파일 {f['stored_files']}개, "
        f"참고 정의 {f['stored_definitions']}개, 호출자 조각 {f['stored_callers']}개",
        f"다시 만든 요청: 묶음 {f['groups']}개, 정의가 붙는 묶음 {f['groups_with_definitions']}개, "
        f"호출자가 붙는 묶음 {f['groups_with_callers']}개, 못 만든 묶음 {f['groups_failed']}개",
    ]
    if pattern:
        lines.append(
            f"보낸 코드의 이름 중 패턴이 든 것 {f['pattern_names_used']}개, "
            f"그중 정의를 찾은 것 {f['pattern_names_defined']}개"
        )
    if not f["rescanned"]:
        lines.append("소스 폴더를 찾지 못해 다시 훑지 않았습니다. 작업을 만든 PC에서 실행하세요.")
        return lines
    lines += [
        f"프로젝트 다시 훑기: .c·.h 파일 {f['project_files']}개",
        f"  줄바꿈이 섞여 못 읽은 파일 {f['unreadable_mixed_newlines']}개",
        f"  인코딩(UTF-8·CP949 아님)으로 못 읽은 파일 {f['unreadable_encoding']}개",
        f"  열지 못한 파일 {f['unreadable_open']}개",
    ]
    if pattern:
        lines.append(
            f"  패턴이 든 파일 {f['pattern_files']}개, "
            f"그중 못 읽은 파일 {f['pattern_unreadable']}개"
        )
    return lines


def clear_screen() -> None:
    # 고정 명령(cls/clear)만 쓰므로 os.system으로 충분하다 (사용자 입력이 섞이지 않는다)
    os.system("cls" if os.name == "nt" else "clear")


def main() -> int:
    parser = argparse.ArgumentParser(prog="peek_job", description="작업 점검 (개수만)")
    parser.add_argument("pattern", nargs="?", help="파일·이름 패턴 (예: rte)")
    parser.add_argument("--job", type=int, help="작업 번호 (빼면 가장 최근)")
    parser.add_argument("--profile", help="llm-fix-server --profile 이름")
    args = parser.parse_args()
    db = Path("private") / (args.profile or "") / "fixer.db"
    try:
        screen = summarize(db, args.job, args.pattern)
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
