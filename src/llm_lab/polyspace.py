"""Polyspace 결과 엑셀에서 RTE(런타임 오류) 행을 읽는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import IO, Any

from openpyxl import load_workbook

REQUIRED_COLUMNS = ("type", "file", "line", "check", "detail")
OPTIONAL_COLUMNS = ("id", "group", "information", "function", "status", "comment")
# 헤더 위에 제목·요약 행이 있어도 찾도록 위에서부터 이만큼 훑는다
HEADER_SCAN_ROWS = 30
# TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}


class SheetError(Exception):
    """RTE 시트나 필수 열을 찾지 못함."""


@dataclass(frozen=True)
class Finding:
    row: int  # 엑셀 행 번호 (1부터)
    color: str  # "Red" | "Orange"
    type: str  # TYPE 칸 원문
    file: str
    line: int | None  # 정수가 아니면 None
    check: str
    detail: str
    id: str = ""
    group: str = ""
    information: str = ""
    function: str = ""
    status: str = ""
    comment: str = ""


@dataclass(frozen=True)
class RteSheet:
    sheet: str
    findings: list[Finding]
    skipped: int  # Gray Check 등 대상이 아니어서 뺀 행 수


def _norm(value: Any) -> str:
    """비교용: 공백을 한 칸으로 줄이고 대소문자를 무시한다."""
    return " ".join(str(value).split()).casefold() if value is not None else ""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _line(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = _text(value)
    return int(text) if text.isdigit() else None


def _header(cells: tuple[Any, ...]) -> dict[str, int] | None:
    """필수 열이 모두 있으면 {열 이름: 위치}를, 아니면 None을 돌려준다."""
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        columns.setdefault(_norm(cell), index)
    return columns if all(name in columns for name in REQUIRED_COLUMNS) else None


def _scan(sheet: Any) -> tuple[int, dict[str, int]] | str:
    """(헤더 행 번호, 열 위치)를, 못 찾으면 오류 안내용으로 처음 보이는 행 내용을 돌려준다."""
    first_seen = ""
    rows = sheet.iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True)
    for number, cells in enumerate(rows, start=1):
        columns = _header(cells)
        if columns is not None:
            return number, columns
        if not first_seen and any(cell is not None for cell in cells):
            first_seen = ", ".join(_text(cell) for cell in cells if cell is not None)
    return first_seen or "(비어 있음)"


def read_rte(source: IO[bytes]) -> RteSheet:
    """엑셀에서 RTE 시트를 찾아 Red/Orange 행을 읽는다."""
    try:
        workbook = load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise SheetError(f"엑셀(.xlsx) 파일로 읽을 수 없습니다: {exc}") from exc
    try:
        found: list[tuple[Any, int, dict[str, int]]] = []
        notes: list[str] = []
        for sheet in workbook.worksheets:
            if not sheet.title.casefold().endswith("_result"):
                notes.append(f"{sheet.title} (이름이 _Result로 끝나지 않음)")
                continue
            scanned = _scan(sheet)
            if isinstance(scanned, str):
                notes.append(f"{sheet.title} (찾은 헤더: {scanned})")
            else:
                found.append((sheet, *scanned))
        if not found:
            raise SheetError(
                "RTE 시트를 찾지 못했습니다. 이름이 _Result로 끝나고 "
                f"{', '.join(REQUIRED_COLUMNS)} 열이 있는 시트가 필요합니다. "
                f"시트: {'; '.join(notes) or '없음'}"
            )
        sheet, header_row, columns = next(
            (item for item in found if "rte" in item[0].title.casefold()), found[0]
        )
        return _read_rows(sheet, header_row, columns)
    finally:
        workbook.close()


def _read_rows(sheet: Any, header_row: int, columns: dict[str, int]) -> RteSheet:
    findings: list[Finding] = []
    skipped = 0
    rows = sheet.iter_rows(min_row=header_row + 1, values_only=True)
    for number, cells in enumerate(rows, start=header_row + 1):
        if all(_text(cell) == "" for cell in cells):
            continue

        def cell(name: str, cells: tuple[Any, ...] = cells) -> Any:
            index = columns.get(name)
            return cells[index] if index is not None and index < len(cells) else None

        color = TARGET_TYPES.get(_norm(cell("type")))
        if color is None:
            skipped += 1
            continue
        findings.append(
            Finding(
                row=number,
                color=color,
                type=_text(cell("type")),
                file=_text(cell("file")),
                line=_line(cell("line")),
                check=_text(cell("check")),
                detail=_text(cell("detail")),
                **{name: _text(cell(name)) for name in OPTIONAL_COLUMNS},
            )
        )
    return RteSheet(sheet=sheet.title, findings=findings, skipped=skipped)
