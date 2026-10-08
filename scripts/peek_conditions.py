"""Polyspace 결과 엑셀의 조건 시트(MISRA 규칙, Code Metrics, RTE)를 한 화면에 보여 준다.

엑셀은 읽기만 한다.

    uv run python scripts/peek_conditions.py "엑셀 경로.xlsx"

이름에 misra·metric·rte가 들어가고 _Result로 끝나지 않는 시트를 조건 시트로 본다.
화면에는 시트마다 앞 몇 행과 값 종류를 찍고, 모든 행은 자르지 않고 임시 폴더 파일에 쓴다.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
import unicodedata
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

KEYWORDS = ("misra", "metric", "rte")
HEADER_SCAN_ROWS = 30
WIDTH = 118
SCREEN_LINES = 40
FEW_VALUES = 8  # 값 종류가 이 이하이고 겹치는 값이 있는 열만 종류별 개수를 보여 준다
MAX_KINDS = 3  # 시트마다 화면에 찍는 값 종류 줄 (조건 시트가 4개 이하일 때)

Row = tuple[int, list[str]]  # (엑셀 행 번호, 칸 글자), 빈 행은 없다


def text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def width(line: str) -> int:
    """콘솔에서 차지하는 칸 수 (한글은 2칸)."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line)


def cut(line: str) -> str:
    """WIDTH 칸에 맞게 자른다('..'). 한글 콘솔에서 2칸으로 그려지는 '…'는 쓰지 않는다."""
    if width(line) <= WIDTH:
        return line
    while width(line) > WIDTH - 2:
        line = line[:-1]
    return line + ".."


def wordy(value: str) -> bool:
    """숫자가 아닌 글자 칸인지 (헤더 추정용)."""
    if not value:
        return False
    try:
        float(value.replace(",", ""))
    except ValueError:
        return True
    return False


def is_condition(title: str) -> bool:
    name = "".join(ch for ch in title.casefold() if ch.isalnum())
    return not title.casefold().endswith("_result") and any(key in name for key in KEYWORDS)


def read(sheet: Any) -> list[Row]:
    rows: list[Row] = []
    for number, cells in enumerate(sheet.iter_rows(values_only=True), start=1):
        values = [text(cell) for cell in cells]
        while values and not values[-1]:
            values.pop()
        if values:
            rows.append((number, values))
    return rows


def kinds(rows: list[Row]) -> tuple[int, list[str]]:
    """(헤더로 보이는 행의 엑셀 번호, 값 종류 줄).

    처음 행들 중 글자 칸이 가장 많은 행을 헤더로 본다.
    """
    if not rows:
        return 0, []
    scan = rows[:HEADER_SCAN_ROWS]
    top = max(range(len(scan)), key=lambda i: (sum(map(wordy, scan[i][1])), -i))
    names = scan[top][1]
    data = [values for _, values in rows[top + 1 :]]
    found: list[tuple[int, int, str]] = []
    for i, name in enumerate(names):
        counter = Counter(values[i] if i < len(values) else "" for values in data)
        if 1 < len(counter) <= FEW_VALUES and len(counter) < len(data):
            listed = ", ".join(f"{value or '(빈칸)'} {n}" for value, n in counter.most_common())
            found.append((len(counter), i, f"{name or f'(열{i + 1})'}: {listed}"))
    return scan[top][0], [line for _, _, line in sorted(found)]


def row_line(row: Row) -> str:
    number, values = row
    return f"  {number:>4}: " + " | ".join(values)


def share(needs: list[int], budget: int) -> list[int]:
    """줄 수를 나눈다. 적게 필요한 시트부터 다 주고 남는 줄을 나머지가 고르게 쓴다."""
    given = [0] * len(needs)
    order = sorted(range(len(needs)), key=lambda i: needs[i])
    for k, i in enumerate(order):
        given[i] = min(needs[i], max(budget, 0) // (len(order) - k))
        budget -= given[i]
    return given


def summarize(path: Path) -> tuple[list[str], list[str]]:
    """(화면 줄, 파일 줄). 화면 줄은 SCREEN_LINES - 1 이하 (마지막 한 줄은 파일 경로)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            raise RuntimeError(f"엑셀(.xlsx)로 열 수 없습니다: {exc}") from exc
        try:
            sheets = [(sheet.title, read(sheet)) for sheet in workbook.worksheets]
        finally:
            workbook.close()

    found = [(title, rows) for title, rows in sheets if is_condition(title)]
    others = [title for title, _ in sheets if not is_condition(title)]
    if not found:
        lines = [
            f"파일: {path.name}",
            "조건 시트를 찾지 못했습니다. 이름에 "
            + ", ".join(KEYWORDS)
            + " 중 하나가 들어가고 _Result로 끝나지 않는 시트를 찾습니다.",
            "시트마다 첫 행:",
            *(
                f"  [{title}] " + (" | ".join(rows[0][1]) if rows else "(비어 있음)")
                for title, rows in sheets
            ),
        ]
        return [cut(line) for line in lines[: SCREEN_LINES - 1]], lines

    head = [
        f"파일: {path.name} / 조건 시트 {len(found)}개: " + ", ".join(t for t, _ in found),
        "그 밖의 시트: " + (", ".join(others) or "없음"),
    ]
    limit = MAX_KINDS if len(found) <= 4 else 0
    described = [(title, rows, *kinds(rows)) for title, rows in found]
    fixed = len(head) + sum(1 + len(lines[:limit]) for *_, lines in described)
    given = share([len(rows) for _, rows, _, _ in described], SCREEN_LINES - 1 - fixed)

    screen, full = list(head), list(head)
    for (title, rows, top, lines), shown in zip(described, given, strict=True):
        guess = f"헤더 추정 {top}행" if top else "비어 있음"
        screen.append(f"[{title}] {len(rows)}행 중 앞 {shown}행 / {guess}")
        screen += [f"  종류 {line}" for line in lines[:limit]]
        screen += [row_line(row) for row in rows[:shown]]
        full += ["", f"[{title}] {len(rows)}행 / {guess}"]
        full += [f"  종류 {line}" for line in lines]
        full += [row_line(row) for row in rows]
    return [cut(line) for line in screen], full


def main() -> int:
    if len(sys.argv) != 2:
        print('사용법: uv run python scripts/peek_conditions.py "엑셀 경로.xlsx"')
        return 2
    path = Path(sys.argv[1].strip().strip('"'))
    out = Path(tempfile.gettempdir()) / "peek_conditions.txt"
    # 화면 지우기는 고정 명령(cls/clear)만 쓰므로 os.system으로 충분하다
    try:
        screen, full = summarize(path)
    except Exception as exc:
        out.write_text(traceback.format_exc(), encoding="utf-8")
        os.system("cls" if os.name == "nt" else "clear")
        print(f"읽지 못했습니다: {path}\n이유: {exc}\n전체 오류: {out}")
        return 1
    out.write_text("\n".join(full) + "\n", encoding="utf-8")
    os.system("cls" if os.name == "nt" else "clear")
    print("\n".join(screen))
    print(f"모든 행(자르지 않음): {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
