"""Polyspace 결과 엑셀의 CodeMetrics 시트를 지표(Check)별로 한 화면에 요약한다 (읽기만 한다).

    uv run python scripts/peek_metrics.py "엑셀 경로.xlsx"

화면은 캡처해서 회사 밖(Claude)으로 보낸다. 그래서 회사 정보는 찍지 않는다: 엑셀 파일 이름, 경로,
소스 파일·함수 이름, Comment 같은 칸 글자, 오류 메시지 원문(경로가 들어 있을 수 있음). 찍는 것은
시트 이름, 알려진 열 이름, Polyspace 지표 이름, 기준치와 값의 숫자, 개수뿐이다.
임시 파일도 쓰지 않는다.
"""

from __future__ import annotations

import os
import sys
import unicodedata
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

REQUIRED = ("file", "function", "line", "check", "threshold", "actual value")
KNOWN = (*REQUIRED, "status", "comment")
HEADER_SCAN_ROWS = 30
WIDTH = 118
SCREEN_LINES = 40
# 지표 표 말고 화면에 찍는 줄: 머리 6줄, 빈 줄, 표 머리, "외 N종", 프롬프트 2줄, 여유 2줄
MAX_CHECKS = SCREEN_LINES - 12
# 화면 글에는 ASCII 구분자만 쓴다. '·'·'…'는 한글 콘솔에서 2칸으로 그려져 줄이 어긋난다.
# 지표 표의 칸: (머리, 폭, 오른쪽 정렬)
COLUMNS = (
    ("Check", 34, False),
    ("행", 5, True),
    ("함수빈칸", 8, True),
    ("Threshold", 18, False),
    ("Actual 최소~최대", 16, False),
    (">기준", 6, True),
    ("=기준", 6, True),
    ("<기준", 6, True),
    ("비교불가", 8, True),
)
# 오류는 종류만 알린다. 원문에는 엑셀 경로나 칸 값이 들어 있을 수 있다.
ERROR_HINTS = {
    "FileNotFoundError": "엑셀 파일이 없습니다. 경로를 확인하세요.",
    "PermissionError": "엑셀을 읽을 수 없습니다. 엑셀 프로그램에서 열려 있으면 닫고 다시 하세요.",
    "InvalidFileException": ".xlsx 파일이 아닙니다.",
    "BadZipFile": ".xlsx 파일이 아니거나 깨졌습니다.",
}


def text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def norm(value: Any) -> str:
    return text(value).casefold()


def width(line: str) -> int:
    """콘솔에서 차지하는 칸 수 (한글은 2칸)."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line)


def fit(value: str, size: int, right: bool = False) -> str:
    """size 칸에 맞게 자르고('..') 남는 칸은 공백으로 채운다."""
    if width(value) > size:
        while width(value) > size - 2:
            value = value[:-1]
        value += ".."
    pad = " " * (size - width(value))
    return pad + value if right else value + pad


def cut(line: str) -> str:
    return fit(line, WIDTH).rstrip()


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(text(value).replace(",", ""))
    except ValueError:
        return None


def show(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


@dataclass
class Found:
    sheet: Any
    header: int  # 헤더 행 번호 (1부터)
    columns: dict[str, int]  # 비교용 열 이름 → 위치
    others: int  # 알려진 열 말고 이름이 있는 열 수 (이름은 찍지 않는다)


@dataclass
class CheckStat:
    rows: int = 0
    no_function: int = 0
    thresholds: Counter[str] = field(default_factory=Counter)
    low: float | None = None
    high: float | None = None
    over: int = 0
    equal: int = 0
    under: int = 0
    unknown: int = 0  # Threshold나 Actual Value가 숫자가 아니어서 비교하지 못함


def find_sheet(workbook: Any) -> Found | list[str]:
    """필요한 열이 모두 있는 시트. 못 찾으면 시트마다 필요한 열을 몇 개 찾았는지 한 줄씩."""
    found: list[Found] = []
    notes: list[str] = []
    others = 0
    for sheet in workbook.worksheets:
        best: set[str] = set()
        rows = sheet.iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True)
        for number_, cells in enumerate(rows, start=1):
            names = [norm(cell) for cell in cells]
            matched = {name for name in REQUIRED if name in names}
            if len(matched) == len(REQUIRED):
                columns = {name: names.index(name) for name in KNOWN if name in names}
                unknown = sum(1 for name in names if name and name not in KNOWN)
                found.append(Found(sheet, number_, columns, unknown))
                break
            best = max(best, matched, key=len)
        else:
            title = sheet.title.casefold()
            if not (title.endswith("_result") or "metric" in title):
                others += 1
                continue
            missing = ", ".join(name for name in REQUIRED if name not in best)
            notes.append(
                f"  [{sheet.title}] 필요한 열 {len(best)}/{len(REQUIRED)}개, 없는 열: {missing}"
            )
    if not found:
        return [*notes, f"  그 밖의 시트 {others}개 (이름은 찍지 않음)"]
    return next((item for item in found if "metric" in item.sheet.title.casefold()), found[0])


def table_line(cells: list[str]) -> str:
    return " ".join(
        fit(cell, size, right) for cell, (_, size, right) in zip(cells, COLUMNS, strict=True)
    ).rstrip()


def check_line(name: str, stat: CheckStat) -> str:
    span = "-" if stat.low is None or stat.high is None else f"{show(stat.low)}~{show(stat.high)}"
    return table_line(
        [
            name,
            str(stat.rows),
            str(stat.no_function),
            ", ".join(value for value, _ in stat.thresholds.most_common()),
            span,
            str(stat.over),
            str(stat.equal),
            str(stat.under),
            str(stat.unknown),
        ]
    )


def summarize(path: Path) -> list[str]:
    """화면 줄. 엑셀을 열지 못하면 예외를 그대로 낸다 (main이 종류만 알린다)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            found = find_sheet(workbook)
            if isinstance(found, list):
                lines = [
                    "CodeMetrics 시트를 찾지 못했습니다. 헤더에 이 열이 모두 있어야 합니다: "
                    + ", ".join(REQUIRED),
                    *found,
                ]
                return [cut(line) for line in lines[: SCREEN_LINES - 2]]
            rows = list(found.sheet.iter_rows(min_row=found.header + 1, values_only=True))
        finally:
            workbook.close()
    return _report(found, rows)


def _report(found: Found, rows: list[tuple[Any, ...]]) -> list[str]:
    def cell(row: tuple[Any, ...], name: str) -> Any:
        index = found.columns.get(name)
        return row[index] if index is not None and index < len(row) else None

    data = [row for row in rows if any(text(value) for value in row)]
    checks: dict[str, CheckStat] = defaultdict(CheckStat)
    functions: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    empty: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    comments = not_number = 0
    for row in data:
        name = text(cell(row, "check")) or "(빈칸)"
        stat = checks[name]
        stat.rows += 1
        function = text(cell(row, "function"))
        if function:
            functions[(norm(cell(row, "file")), function)].append((text(cell(row, "line")), name))
        else:
            stat.no_function += 1
        for column in ("function", "line", "threshold", "actual value"):
            empty[column] += not text(cell(row, column))
        stat.thresholds[text(cell(row, "threshold")) or "(빈칸)"] += 1
        threshold, actual = number(cell(row, "threshold")), number(cell(row, "actual value"))
        not_number += actual is None
        if actual is not None:
            stat.low = actual if stat.low is None else min(stat.low, actual)
            stat.high = actual if stat.high is None else max(stat.high, actual)
        if threshold is None or actual is None:
            stat.unknown += 1
        elif actual > threshold:
            stat.over += 1
        elif actual == threshold:
            stat.equal += 1
        elif actual < threshold:
            stat.under += 1
        else:  # NaN
            stat.unknown += 1
        statuses[text(cell(row, "status")) or "(빈칸)"] += 1
        comments += bool(text(cell(row, "comment")))

    sizes = Counter(min(len(items), 5) for items in functions.values())
    spread = " / ".join(
        f"{size}개{'+' if size == 5 else ''} {sizes[size]}" for size in sorted(sizes)
    )
    differing = sum(len({line for line, _ in items}) > 1 for items in functions.values())
    repeated = sum(len(items) != len({check for _, check in items}) for items in functions.values())
    columns = ", ".join(name for name in KNOWN if name in found.columns)
    head = [
        f"시트: {found.sheet.title} (헤더 {found.header}행) / 데이터 {len(data)}행 / "
        f"Check {len(checks)}종",
        f"열: {columns}" + (f" (+ 다른 열 {found.others}개)" if found.others else ""),
        f"Function 빈 행 {empty['function']}, line 빈 행 {empty['line']}, "
        f"Threshold 빈 행 {empty['threshold']}, Actual Value 숫자 아닌 행 {not_number}",
        f"함수(File+Function) {len(functions)}개, 함수당 행 수: {spread or '-'}",
        f"같은 함수인데 line이 다른 함수 {differing}개, 같은 Check가 반복된 함수 {repeated}개",
        "Status: "
        + ", ".join(f"{value} {n}" for value, n in statuses.most_common(6))
        + f" / Comment 있는 행 {comments} (내용은 찍지 않음)",
        "",
        table_line([title for title, _, _ in COLUMNS]),
    ]
    ordered = sorted(checks.items(), key=lambda item: (-item[1].rows, item[0]))
    table = [check_line(name, stat) for name, stat in ordered]
    screen = [cut(line) for line in head] + table[:MAX_CHECKS]
    if len(table) > MAX_CHECKS:
        screen.append(f"... 외 {len(table) - MAX_CHECKS}종 (행 수가 적은 지표)")
    return screen


def clear_screen() -> None:
    # 고정 명령(cls/clear)만 쓰므로 os.system으로 충분하다
    os.system("cls" if os.name == "nt" else "clear")


def main() -> int:
    if len(sys.argv) != 2:
        print('사용법: uv run python scripts/peek_metrics.py "엑셀 경로.xlsx"')
        return 2
    path = Path(sys.argv[1].strip().strip('"'))
    try:
        screen = summarize(path)
    except Exception as exc:
        kind = type(exc).__name__
        clear_screen()
        print(f"요약하지 못했습니다: {kind}")
        print(ERROR_HINTS.get(kind, "오류 원문은 경로나 칸 값이 들어 있을 수 있어 찍지 않습니다."))
        return 1
    clear_screen()
    print("\n".join(screen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
