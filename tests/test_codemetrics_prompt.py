import io

import pytest
from codemetrics_fixtures import GRADE_C, metric_row, metrics_excel

from llm_lab.fixer import FixError, make_request
from llm_lab.kinds import codemetrics
from llm_lab.kinds.codemetrics import CODEMETRICS
from llm_lab.polyspace import read_sheet
from llm_lab.sources import decode_source

SOURCE = decode_source("src/grade.c", GRADE_C)


def job(*rows: list):
    """(엑셀에서 읽은 대상 행들, 작업 정보)."""
    sheet = read_sheet(io.BytesIO(metrics_excel(*rows)), CODEMETRICS, CODEMETRICS.defaults)
    return sheet.findings, sheet.prepared.data


def test_message_lists_values_limits_tips_and_the_lines_to_replace():
    findings, context = job(
        metric_row(actual=24),
        metric_row("Number of Calling Functions", threshold=4, actual=6),
        metric_row("Number of Called Functions", threshold=6, actual=9, function="other()"),
    )

    request = make_request(CODEMETRICS, findings[:2], SOURCE, context)
    system, user = (message["content"] for message in request.messages)

    assert system == CODEMETRICS.system_prompt
    assert "Answer with JSON only" in system
    assert user.startswith("Polyspace code metrics over their limits in one C function:")
    assert (
        "Finding 1:\n- check: Cyclomatic Complexity\n- Actual Value: 24\n- Threshold: 10\n"
        "- stage: Fail (고칠 것)\n- Function: grade()\n- File: src/grade.c\n- line: 4"
    ) in user
    # 한도 표에는 조건 시트의 모든 지표와 짝 없는 Check가 들어간다
    assert "- Cyclomatic Complexity: Pass range 1 ~ 10" in user
    assert "- Number of Called Functions: Threshold 6" in user
    # 요령은 이 묶음의 지표만
    assert "Cyclomatic Complexity counts decision points" in user
    assert "Number of Calling Functions counts the distinct callers" in user
    assert "Number of Called Functions counts" not in user
    assert "Replace lines 3-15 of src/grade.c (function grade and the comment" in user
    assert "Code (lines 1-15 of src/grade.c;" in user  # 파일이 짧아 파일 전체를 보인다
    assert (request.first, request.last) == (1, 15)


def test_long_files_show_only_the_function(monkeypatch):
    monkeypatch.setattr(codemetrics, "WHOLE_FILE_MAX_LINES", 10)
    findings, context = job(metric_row())

    request = make_request(CODEMETRICS, findings, SOURCE, context)

    assert (request.first, request.last) == (3, 15)


def test_function_not_in_the_source_is_a_row_error():
    findings, context = job(metric_row(function="missing()"))

    with pytest.raises(FixError) as caught:
        make_request(CODEMETRICS, findings, SOURCE, context)

    assert str(caught.value).startswith("엑셀의 함수를 소스에서 찾지 못했습니다.")


def test_very_long_function_is_not_sent(monkeypatch):
    monkeypatch.setattr(codemetrics, "FUNCTION_MAX_LINES", 12)
    findings, context = job(metric_row())

    with pytest.raises(FixError) as caught:
        make_request(CODEMETRICS, findings, SOURCE, context)

    message = str(caught.value)
    assert message.startswith("함수가 너무 길어 자동으로 나누지 않았습니다.")
    assert "13줄 (기준 12줄)" in message


def test_limits_come_from_the_job_not_from_code():
    excel = metrics_excel(metric_row(threshold=99, actual=100), conditions=None)
    sheet = read_sheet(io.BytesIO(excel), CODEMETRICS, ["Unknown"])

    request = make_request(CODEMETRICS, sheet.findings, SOURCE, sheet.prepared.data)

    assert "- Cyclomatic Complexity: Threshold 99" in request.messages[1]["content"]


def test_rows_of_one_group_on_different_definitions_are_an_error():
    # #if 갈래마다 같은 이름의 정의가 있으면 묶음 키(파일:이름)가 같아도 다른 함수다
    source = decode_source(
        "src/g.c",
        b"#if A\nstatic int g(void)\n{\n    return 1;\n}\n"
        b"#else\nstatic int g(void)\n{\n    return 2;\n}\n#endif\n",
    )
    findings, context = job(
        metric_row(function="g()", line=2),
        metric_row("Number of Calling Functions", threshold=4, actual=6, function="g()", line=7),
    )

    with pytest.raises(FixError) as caught:
        make_request(CODEMETRICS, findings, source, context)

    message = str(caught.value)
    assert message.startswith("같은 함수의 행이 서로 다른 줄을 가리킵니다.")
    assert "엑셀 3행: 7번 줄 (함수 범위 2~5줄)" in message
