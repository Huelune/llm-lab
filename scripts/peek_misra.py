"""Polyspace 결과 엑셀의 MISRA 행을 규칙 장(1~22, Dir 1~4)별로, 빠진 이유와 분류로 센다.

    uv run python scripts/peek_misra.py "엑셀 경로.xlsx"

서버가 행을 읽는 규칙(kinds/misra.py read_row)을 그대로 쓴다. 조건 시트에서 꺼진 규칙은
"조건 시트에서 꺼짐", 나머지는 분류(Mandatory / Required / Advisory / 분류 없음)로 센다.
첫 화면에서 고른 분류만 분석하므로(기본은 Mandatory·Required), 어느 장이 왜 빠졌는지 볼 수 있다.

화면은 캡처해서 회사 밖(Claude)으로 보낸다. 그래서 규칙 장 번호(공개 표준 번호), 분류, 개수만
찍는다. 엑셀 파일 이름, 시트 이름, 칸의 글, 오류 원문은 찍지 않는다.
"""

from __future__ import annotations

import os
import sys
import warnings
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

from llm_lab.kinds.misra import CATEGORIES, DIRECTIVE, DISABLED, MISRA, NO_CATEGORY, rule_key
from llm_lab.polyspace import Finding, cell_text, data_rows, find_header

NO_KEY = "번호 없음"


def _chapter(key: str | None) -> str:
    if key is None:
        return NO_KEY
    major = key.split(".", 1)[0]
    return f"Dir {major[1:]}" if major.startswith("D") else major


def chapter_counts(path: Path) -> dict[str, dict[str, int]]:
    """장 → {"rows", "disabled", 분류별 수}. MISRA 결과 시트가 없으면 LookupError."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            title = sheet.title.casefold()
            if not title.endswith("_result") or MISRA.sheet_word not in title:
                continue
            found = find_header(sheet, MISRA.required)
            if isinstance(found, str):
                continue
            prepared = MISRA.prepare(workbook)
            counts: dict[str, Counter[str]] = {}
            for number, cell in data_rows(sheet, *found):
                group = cell_text(cell("group"))
                key = rule_key(cell_text(cell("check")), DIRECTIVE.search(group) is not None)
                chapter = counts.setdefault(_chapter(key), Counter(rows=0, disabled=0))
                chapter["rows"] += 1
                result = MISRA.read_row(cell, number, prepared.data)
                if isinstance(result, Finding):
                    chapter[result.category or NO_CATEGORY] += 1
                elif result == DISABLED:
                    chapter["disabled"] += 1
                else:
                    chapter["other"] += 1
            return {name: dict(counter) for name, counter in counts.items()}
    finally:
        workbook.close()
    raise LookupError("MISRA 결과 시트 없음")


def _order(name: str) -> tuple[int, int]:
    if name == NO_KEY:
        return (2, 0)
    if name.startswith("Dir "):
        return (1, int(name[4:]) if name[4:].isdigit() else 0)
    return (0, int(name) if name.isdigit() else 0)


def summarize(path: Path) -> list[str]:
    if not path.is_file():
        return ["엑셀 파일을 찾지 못했습니다. 경로를 따옴표로 감싸 다시 실행하세요."]
    try:
        counts = chapter_counts(path)
    except LookupError:
        return [
            "MISRA 결과 시트를 찾지 못했습니다.",
            "이름이 _Result로 끝나고 misra가 들어가며",
            "TYPE·File·Line·Check·Detail 열이 있어야 합니다.",
        ]
    lines = ["MISRA 결과 행 (규칙 장별, 서버와 같은 규칙으로 읽음):"]
    for name in sorted(counts, key=_order):
        item = counts[name]
        label = name if name == NO_KEY or name.startswith("Dir") else f"규칙 {name}장"
        parts = [f"행 {item['rows']}", f"조건 시트에서 꺼짐 {item.get('disabled', 0)}"]
        parts += [f"{c} {item[c]}" for c in (*CATEGORIES, NO_CATEGORY) if item.get(c)]
        if item.get("other"):
            parts.append(f"그 밖 {item['other']}")
        lines.append(f"  {label}: " + ", ".join(parts))
    lines += [
        "",
        "첫 화면 기본 선택은 Mandatory·Required입니다.",
        "Advisory만 있는 장은 Advisory를 체크해야 분석합니다.",
    ]
    return lines


def clear_screen() -> None:
    # 고정 명령(cls/clear)만 쓰므로 os.system으로 충분하다
    os.system("cls" if os.name == "nt" else "clear")


def main() -> int:
    if len(sys.argv) != 2:
        print('사용법: uv run python scripts/peek_misra.py "엑셀.xlsx"')
        return 1
    try:
        screen = summarize(Path(sys.argv[1]))
    except Exception as exc:
        clear_screen()
        print(f"엑셀을 읽지 못했습니다 (오류 종류: {type(exc).__name__}).")
        print("엑셀 프로그램에서 열리는 .xlsx 파일인지, 열려 있지 않은지 확인하세요.")
        return 1
    clear_screen()
    print("\n".join(screen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
