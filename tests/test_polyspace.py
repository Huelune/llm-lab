import io
from dataclasses import replace

import pytest
from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_row
from rte_fixtures import RTE_HEADER, rte_excel, rte_row, workbook_bytes

from llm_lab.kinds import MISRA, RTE
from llm_lab.polyspace import Finding, Prepared, SheetError, read_sheet


def read(data: bytes, selected=("Red", "Orange")):
    return read_sheet(io.BytesIO(data), RTE, selected)


def test_reads_red_and_orange_and_counts_skipped_rows_by_reason():
    data = rte_excel(
        rte_row("Red Check", line=7, check="Overflow"),
        rte_row("Gray Check"),
        rte_row("Orange Check", line=12),
        rte_row("Gray Check"),
        rte_row(None),
    )

    sheet = read(data)

    assert sheet.sheet == "RTE_Result"
    assert sheet.skipped == {"Gray Check": 2, "(빈 TYPE)": 1}
    assert [(f.row, f.color, f.line, f.check) for f in sheet.findings] == [
        (2, "Red", 7, "Overflow"),
        (4, "Orange", 12, "Division by zero"),
    ]
    first = sheet.findings[0]
    assert first.file == r"C:\proj\src\calc.c"
    assert first.function == "divide"
    assert first.status == "Unreviewed"
    assert first.id == "1"


def test_unselected_targets_are_skipped_with_their_label():
    data = rte_excel(rte_row("Red Check"), rte_row("Orange Check"), rte_row("Orange Check"))

    sheet = read(data, selected=["Red"])

    assert [f.color for f in sheet.findings] == ["Red"]
    assert sheet.skipped == {"Orange (고르지 않음)": 2}


def test_finds_header_below_title_rows_and_ignores_blank_rows():
    data = rte_excel(
        rte_row(line=5),
        [None] * len(RTE_HEADER),
        rte_row(line=6),
        preamble=(["RTE results"], ["generated 2026-10-07"], []),
    )

    sheet = read(data)

    assert [(f.row, f.line) for f in sheet.findings] == [(5, 5), (7, 6)]
    assert sheet.skipped == {}


def test_header_match_ignores_case_spaces_line_breaks_and_trailing_colons():
    # 엑셀 셀 안 줄바꿈(Alt+Enter), 앞뒤 공백, 끝의 콜론이 있어도 같은 열로 본다
    header = [" type ", "FILE\n", "Line :", "CHECK:", "Detail"]
    data = workbook_bytes({"RTE_Result": [header, ["red  check", "a.c", " 57 ", "Overflow", "x"]]})

    sheet = read(data)

    assert sheet.findings[0].color == "Red"
    assert sheet.findings[0].line == 57
    assert sheet.findings[0].check == "Overflow"
    assert sheet.findings[0].group == ""  # 없는 선택 열은 빈 문자열


def test_sheet_is_chosen_by_name_because_kinds_share_columns():
    rows = [RTE_HEADER, rte_row()]
    data = workbook_bytes({"MISRA_Result": rows, "RTE_Result": rows, "Other": rows})

    assert read(data).sheet == "RTE_Result"

    with pytest.raises(SheetError) as caught:
        read(workbook_bytes({"MISRA_Result": rows}))
    message = str(caught.value)
    assert message.startswith("RTE 시트를 찾지 못했습니다.")
    assert "MISRA_Result (이름에 rte가 없음)" in message


def test_misra_sheet_uses_the_condition_sheet():
    data = workbook_bytes(
        {
            "RTE_Result": [RTE_HEADER, rte_row()],
            "MISRA_Result": [
                MISRA_HEADER,
                misra_row("10.4 Operands", category="Advisory"),  # 조건 시트에서는 Required
                misra_row("2.2 Dead code"),  # 조건 시트에서 꺼짐
                misra_row("12.1 Precedence", category="Required"),  # 조건 시트에서는 Advisory
                misra_row("21.3 Memory", category="Mandatory"),  # 조건 시트에 없음
                misra_row("9.1 Init", category="Required"),  # 조건 시트 분류가 표준 밖이라 늘 남음
            ],
            "Rules": CONDITION_ROWS,
        }
    )

    sheet = read_sheet(io.BytesIO(data), MISRA, MISRA.defaults)

    assert sheet.sheet == "MISRA_Result"
    assert [(f.row, f.category) for f in sheet.findings] == [
        (2, "Required"),
        (5, "Mandatory"),
        (6, "Disapplied"),
    ]
    assert sheet.findings[0].note == "분류: 조건 시트 Required (결과 시트 Advisory)"
    assert sheet.skipped == {"적용 안 함 (조건 시트)": 1, "Advisory (고르지 않음)": 1}
    assert sheet.prepared.note == "조건 시트: 규칙 5개"


def test_misra_sheet_without_condition_sheet_uses_its_own_categories():
    data = workbook_bytes(
        {"MISRA_Result": [MISRA_HEADER, misra_row(category="Advisory"), misra_row()]}
    )

    sheet = read_sheet(io.BytesIO(data), MISRA, ["Required", "Advisory"])

    assert [f.category for f in sheet.findings] == ["Advisory", "Required"]
    assert sheet.prepared.note == "조건 시트 없음: 결과 시트 분류를 씀"


def test_columns_are_found_by_name_wherever_they_are():
    # 같은 머리 이름이 다른 자리에 있고, 사이에 모르는 열이 끼어 있어도 이름으로 찾는다
    header = ["line", "Extra", "detail", "check", "Function", "File", "Note", "TYPE"]
    row = [42, "x", "detail text", "Overflow", "divide", "a.c", "y", "Red Check"]
    data = workbook_bytes({"RTE_Result": [header, row]})

    found = read(data).findings[0]

    assert (found.color, found.line, found.check, found.file, found.function) == (
        "Red",
        42,
        "Overflow",
        "a.c",
        "divide",
    )
    assert found.detail == "detail text"


def test_non_integer_line_becomes_none():
    data = rte_excel(rte_row(line="12"), rte_row(line=12.0), rte_row(line="n/a"))

    assert [f.line for f in read(data).findings] == [12, 12, None]


def test_missing_required_column_lists_sheets_and_headers():
    data = workbook_bytes(
        {
            "Summary": [["x"]],
            "RTE_Result": [["ID", "Rule", "File", "Line"], [1, "10.1", "a.c", 3]],
        }
    )

    with pytest.raises(SheetError) as caught:
        read(data)

    message = str(caught.value)
    assert "Summary (이름이 _Result로 끝나지 않음)" in message
    assert "RTE_Result (찾은 헤더: ID, Rule, File, Line)" in message


def test_not_an_excel_file():
    with pytest.raises(SheetError, match="엑셀"):
        read(b"not a zip file")


def test_prepare_reads_the_whole_workbook_once_and_feeds_every_row():
    seen = []

    def prepare(workbook):
        seen.append(sorted(sheet.title for sheet in workbook.worksheets))
        return Prepared({"limit": 10}, "메모")

    def read_row(cell, row, context):
        found = RTE.read_row(cell, row, context)
        if isinstance(found, Finding):
            found = replace(found, note=f"limit {context['limit']}")
        return found

    kind = replace(RTE, prepare=prepare, read_row=read_row)
    data = workbook_bytes({"Summary": [["x"]], "RTE_Result": [RTE_HEADER, rte_row(), rte_row()]})

    sheet = read_sheet(io.BytesIO(data), kind, kind.defaults)

    assert seen == [["RTE_Result", "Summary"]]
    assert sheet.prepared == Prepared({"limit": 10}, "메모")
    assert [f.note for f in sheet.findings] == ["limit 10", "limit 10"]
