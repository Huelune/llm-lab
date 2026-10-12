"""CodeMetrics 테스트용 데이터. 값은 모두 만들어 낸 것이다 (회사 엑셀의 값을 넣지 않는다)."""

import json

import httpx2
from fake_llm import completion
from rte_fixtures import workbook_bytes

GRADE_PATH = r"C:\proj\src\grade.c"
METRICS_HEADER = [
    "File",
    "Function",
    "line",
    "Check",
    "Threshold",
    "Actual Value",
    "Status",
    "Comment",
]

# 조건 시트: 제목 행과 빈 행 아래에 헤더가 있고, 병합 셀이 남긴 빈 열이 끼어 있고, 지표 이름은
# 첫 단계 행에만 있고, 범위 글 모양이 섞여 있다. 지표 이름은 결과 시트와 다르다:
# "Max. Number of Calling Functions"는 낱말로, "Deepest Nesting"은 값(Pass 끝값 3)으로 잇는다.
CONDITION_ROWS = [
    ["코드 지표 기준"],
    [],
    ["No", None, "지표", None, "설명", "기준", None, "판정"],
    [1, None, "Cyclomatic Complexity", None, "분기 수", "1 ~ 10", None, "Pass"],
    [None, None, None, None, None, "11 ~ 20", None, "검토 후 결정"],
    [None, None, None, None, None, ">= 21", None, "Fail (고칠 것)"],
    [2, None, "Deepest Nesting", None, "겹침", "0~3", None, "Pass"],
    [None, None, None, None, None, ">=4", None, "Fail (고칠 것)"],
    [3, None, "Max. Number of Calling Functions", None, "부르는 곳", "0 ~ 4", None, "Pass"],
    [None, None, None, None, None, "5 ~ 8", None, "검토 후 결정"],
    [None, None, None, None, None, "> 8", None, "Fail (고칠 것)"],
]

GRADE_C = (
    b'#include "grade.h"\n'
    b"\n"
    b"/* \xc1\xa1\xbc\xf6\xb8\xa6 \xb5\xee\xb1\xde\xc0\xb8\xb7\xce \xb9\xd9\xb2\xdb\xb4\xd9. */\n"
    b"int grade(int score)\n"
    b"{\n"
    b"    int result = 0;\n"
    b"    if (score > 90) {\n"
    b"        result = 4;\n"
    b"    } else if (score > 80) {\n"
    b"        result = 3;\n"
    b"    } else {\n"
    b"        result = 1;\n"
    b"    }\n"
    b"    return result;\n"
    b"}\n"
)  # CP949로 저장한 파일 ("/* 점수를 등급으로 바꾼다. */")

# LLM이 낼 리팩터링: 도우미를 범위 맨 위에 두고, 원래 주석과 시그니처는 그대로 둔다
GRADE_REFACTORED = """\
/* 점수 구간에 맞는 등급. */
static int grade_band(int score)
{
    int band = 1;
    if (score > 90) {
        band = 4;
    } else if (score > 80) {
        band = 3;
    } else {
        band = 1;
    }
    return band;
}

/* 점수를 등급으로 바꾼다. */
int grade(int score)
{
    int result = 0;
    result = grade_band(score);
    return result;
}
"""


def metric_row(
    check: str = "Cyclomatic Complexity",
    *,
    threshold: object = 10,
    actual: object = 24,
    function: str = "grade()",
    line: object = 4,
    file: str = GRADE_PATH,
) -> list:
    return [file, function, line, check, threshold, actual, "Unreviewed", None]


def metrics_excel(*rows: list, conditions: list[list] | None = CONDITION_ROWS) -> bytes:
    """표지, CodeMetrics 결과 시트, (있으면) 조건 시트가 든 엑셀."""
    sheets: dict[str, list[list]] = {
        "Cover": [["Polyspace report"]],
        "Metrics_Result": [METRICS_HEADER, *rows],
    }
    if conditions is not None:
        sheets["Metric Bands"] = conditions
    return workbook_bytes(sheets)


def code_answer(
    *findings: tuple[str, str], code: str = "", finish_reason: str = "stop"
) -> httpx2.Response:
    """CodeMetrics 답. findings는 1번부터 차례로 (판단, 이유)."""
    body = {
        "findings": [
            {"number": number, "decision": decision, "reason": reason}
            for number, (decision, reason) in enumerate(findings, start=1)
        ],
        "code": code,
    }
    return completion(json.dumps(body, ensure_ascii=False), finish_reason=finish_reason)
