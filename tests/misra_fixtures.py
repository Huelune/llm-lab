"""MISRA 테스트용 데이터. 값은 모두 만들어 낸 것이다 (회사 엑셀의 값을 넣지 않는다)."""

import io

from openpyxl import load_workbook
from rte_fixtures import CALC_PATH

from llm_lab.polyspace import Finding

MISRA_HEADER = [
    "TYPE",
    "Group",
    "information",
    "File",
    "Function",
    "Line",
    "Check",
    "Detail",
    "Status",
    "Comment",
]

# 조건 시트: 이름도 열 배치도 결과 시트와 다르다. 첫 열은 비어 있고, 헤더 끝에 콜론이 붙고,
# Mode의 대소문자가 섞이고, 꺼진 규칙과 빈 줄과 Mode가 빈 줄이 있다.
CONDITION_ROWS = [
    ["규칙 목록"],
    [None, "Guideline", "Description", "MODE", "Enabled:"],
    [None, "10.4", "operands", "required", "yes"],
    [None, "Rule 12.1", "precedence", "ADVISORY", "Yes"],
    [None, "2.2", "dead code", "advisory", "no"],
    [None, "Dir 4.6", "basic types", "Mandatory", None],
    [None, "9.1", "init", "disapplied", "YES"],
    [None, None, None, None, None],
    [None, "17.7", "return value", None, "yes"],
]


def misra_row(
    check: str = "10.4 Operands shall share an essential type category",
    *,
    category: str = "Required",
    line: object = 3,
    file: str = CALC_PATH,
    group: str = "10 The essential type model",
) -> list:
    information = f"Category: {category}" if category else ""
    detail = "Operands have different essential types"
    return ["MISRA C:2012", group, information, file, "divide", line, check, detail, "", ""]


def misra_finding(**overrides: object) -> Finding:
    """misra_row() 기본값과 같은 행."""
    values: dict = {
        "row": 2,
        "color": "",
        "type": "MISRA C:2012",
        "file": CALC_PATH,
        "line": 3,
        "check": "10.4 Operands shall share an essential type category",
        "detail": "Operands have different essential types",
        "group": "10 The essential type model",
        "information": "Category: Required",
        "function": "divide",
        "category": "Required",
    }
    return Finding(**{**values, **overrides})


def open_workbook(data: bytes):
    """read_sheet가 여는 것과 같은 방식(읽기 전용)으로 연 엑셀."""
    return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
