"""RTE 테스트용 데이터: Polyspace 엑셀, 소스 파일, LLM 답변."""

import io

from openpyxl import Workbook

CALC_PATH = r"C:\proj\src\calc.c"

RTE_HEADER = [
    "ID",
    "TYPE",
    "Group",
    "information",
    "File",
    "Function",
    "line",
    "check",
    "detail",
    "status",
    "comment",
]


def rte_row(
    type_: str = "Orange Check",
    *,
    file: str = CALC_PATH,
    line: object = 3,
    check: str = "Division by zero",
    detail: str = "Warning: scalar division by zero may occur",
    function: str = "divide",
) -> list:
    return [1, type_, "Numerical", "", file, function, line, check, detail, "Unreviewed", ""]


def workbook_bytes(sheets: dict[str, list[list]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rte_excel(*rows: list, preamble: tuple[list, ...] = ()) -> bytes:
    """요약 시트 하나와 RTE_Result 시트(제목 행 preamble, 헤더, rows)가 있는 엑셀."""
    return workbook_bytes(
        {"Summary": [["Polyspace report"]], "RTE_Result": [*preamble, RTE_HEADER, *rows]}
    )
