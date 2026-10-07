"""RTE 테스트용 데이터: Polyspace 엑셀, 소스 파일, LLM 답변."""

import io
import json

import httpx2
from fake_llm import completion
from openpyxl import Workbook

from llm_lab.polyspace import Finding

CALC_PATH = r"C:\proj\src\calc.c"
CALC_C = b"int divide(int a, int b)\n{\n    return a / b;\n}\n"
DIVIDE_FIX = {"original": "    return a / b;", "replacement": "    return (b != 0) ? a / b : 0;"}
REASON = "b가 0일 때를 검사합니다."

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


def finding(**overrides: object) -> Finding:
    """rte_row() 기본값과 같은 행."""
    values: dict = {
        "row": 2,
        "color": "Orange",
        "type": "Orange Check",
        "file": CALC_PATH,
        "line": 3,
        "check": "Division by zero",
        "detail": "Warning: scalar division by zero may occur",
        "group": "Numerical",
        "function": "divide",
        "status": "Unreviewed",
        "id": "1",
    }
    return Finding(**{**values, **overrides})


def fix_answer(
    decision: str = "fix", edits: list[dict] | None = None, *, finish_reason: str = "stop"
) -> httpx2.Response:
    """LLM의 json_schema 답변. edits를 안 주면 calc.c의 0 나누기 수정."""
    if edits is None:
        edits = [DIVIDE_FIX] if decision == "fix" else []
    body = {"decision": decision, "reason": REASON, "edits": edits}
    return completion(json.dumps(body, ensure_ascii=False), finish_reason=finish_reason)
