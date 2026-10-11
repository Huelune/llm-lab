"""RTE(런타임 오류) 결과 시트: Polyspace Code Prover의 Red·Orange 검사."""

from __future__ import annotations

from typing import Any

from llm_lab.kinds.base import Cell, Kind
from llm_lab.polyspace import Finding, cell_text, line_number, norm

# TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}


def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
    """Red·Orange Check면 Finding, 그 밖(Gray Check 등)이면 TYPE 글자가 뺀 이유다."""
    color = TARGET_TYPES.get(norm(cell("type")))
    if color is None:
        return cell_text(cell("type")) or "(빈 TYPE)"
    return Finding(
        row=row,
        color=color,
        type=cell_text(cell("type")),
        file=cell_text(cell("file")),
        line=line_number(cell("line")),
        check=cell_text(cell("check")),
        detail=cell_text(cell("detail")),
        id=cell_text(cell("id")),
        group=cell_text(cell("group")),
        information=cell_text(cell("information")),
        function=cell_text(cell("function")),
        status=cell_text(cell("status")),
        comment=cell_text(cell("comment")),
    )


def color_of(finding: Finding) -> str:
    return finding.color


RTE = Kind(
    name="rte",
    title="RTE",
    sheet_word="rte",
    required=("type", "file", "line", "check", "detail"),
    read_row=read_row,
    label=color_of,
    choices=("Red", "Orange"),
    defaults=("Red", "Orange"),
    colors={"Red": "red", "Orange": "orange"},
)
