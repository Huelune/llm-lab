import io

from codemetrics_fixtures import CONDITION_ROWS, metric_row, metrics_excel

from llm_lab.conditions import Metric, read_metrics
from llm_lab.kinds import KINDS
from llm_lab.kinds.codemetrics import (
    CODEMETRICS,
    FAIL,
    MIDDLE,
    NO_FUNCTION,
    PASSED,
    UNKNOWN,
    metric_words,
    pair_metrics,
)
from llm_lab.polyspace import read_sheet


def read(data: bytes, selected=(FAIL, MIDDLE, UNKNOWN)):
    return read_sheet(io.BytesIO(data), CODEMETRICS, selected)


ROWS = [
    metric_row(actual=24),  # 2행: 21 이상 → Fail
    metric_row(actual=15, function="other()", line=20),  # 3행: 11~20 → Conditional Pass
    metric_row(actual=8, function="third()", line=30),  # 4행: 1~10 → Pass라 뺀다
    metric_row("Number of Call Levels", threshold=3, actual=5),  # 5행: 값으로 이음, 4 이상 → Fail
    metric_row("Number of Calling Functions", threshold=4, actual=6),  # 6행: 낱말로 이음 → Conditional Pass
    metric_row("Number of Called Functions", threshold=6, actual=9),  # 7행: 조건 시트에 없음
    metric_row(function="", line=None),  # 8행: 파일 단위 지표
]


def test_targets_get_their_stage_from_the_condition_sheet():
    sheet = read(metrics_excel(*ROWS))

    assert sheet.sheet == "Metrics_Result"
    assert [(f.row, f.category, f.stage) for f in sheet.findings] == [
        (2, FAIL, "Fail (고칠 것)"),
        (3, MIDDLE, "검토 후 결정"),
        (5, FAIL, "Fail (고칠 것)"),
        (6, MIDDLE, "검토 후 결정"),
        (7, UNKNOWN, ""),
    ]
    assert sheet.skipped == {PASSED: 1, NO_FUNCTION: 1}
    first = sheet.findings[0]
    assert (first.threshold, first.actual, first.function) == ("10", "24", "grade()")
    assert first.note == "값 24 · 기준 10 · 조건 시트: Fail (고칠 것)"
    assert sheet.findings[-1].note == "값 9 · 기준 6 · Unknown: 조건 시트에 짝이 없는 지표"
    assert sheet.prepared.note == (
        "조건 시트: 지표 3개 · 조건 시트에서 짝을 찾지 못한 지표 1개 · 값으로 이은 지표 1개"
    )


def test_without_a_condition_sheet_every_row_is_a_target_of_unknown_stage():
    sheet = read(metrics_excel(metric_row(actual=3), conditions=None))

    assert [(f.category, f.note) for f in sheet.findings] == [
        (UNKNOWN, "값 3 · 기준 10 · Unknown: 조건 시트 없음")
    ]
    assert sheet.prepared.note == "조건 시트 없음: 결과 시트 Threshold만 씀"


def test_values_outside_every_band_or_not_numbers_are_unknown_with_a_reason():
    conditions = [
        ["No", "지표", "기준", "판정"],
        [1, "Cyclomatic Complexity", "1 ~ 10", "Pass"],
        [None, None, "많음", "Fail"],
    ]
    rows = [metric_row(actual=30), metric_row(actual="n/a")]
    sheet = read(metrics_excel(*rows, conditions=conditions))

    assert [f.note.split("Unknown: ")[1] for f in sheet.findings] == [
        "범위 글을 읽지 못함",
        "Actual Value가 숫자가 아님",
    ]


def test_unselected_stages_are_skipped():
    sheet = read(metrics_excel(*ROWS), selected=[FAIL])

    assert [f.row for f in sheet.findings] == [2, 5]
    assert sheet.skipped[f"{MIDDLE} (고르지 않음)"] == 2


def test_metric_words_ignore_case_plural_punctuation_and_common_words():
    assert metric_words("Max. Number of Calling Functions") == {"calling", "function"}
    assert metric_words("Number of Calling Functions") == metric_words("calling function")
    assert metric_words("Number of Called Functions") != metric_words("Calling Functions")


def bands(*pairs: tuple[str, str]) -> list[list]:
    rows = [["No", "지표", "기준", "판정"]]
    for number, (name, edge) in enumerate(pairs, start=1):
        rows += [[number, name, f"0 ~ {edge}", "Pass"], [None, None, f"> {edge}", "Fail"]]
    return rows


def condition_metrics(rows: list[list]) -> list[Metric]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(metrics_excel(conditions=rows)), read_only=True)
    return read_metrics(workbook)[0]


def test_pairing_by_value_needs_one_match_in_both_directions():
    metrics = condition_metrics(bands(("Nesting A", "3"), ("Nesting B", "5")))

    pairs, by_value = pair_metrics({"Level One": ["3"], "Level Two": ["5"]}, metrics)
    assert pairs == {"level one": 0, "level two": 1}
    assert by_value == ["Level One", "Level Two"]

    # 같은 한도의 Check가 둘이면 어느 쪽인지 모르므로 잇지 않는다
    pairs, _ = pair_metrics({"Level One": ["3"], "Level Two": ["3"]}, metrics)
    assert pairs == {}
    # 한도가 두 가지인 Check도 잇지 않는다
    assert pair_metrics({"Level One": ["3", "5"]}, metrics)[0] == {}


def test_limits_name_both_sheets_and_keep_thresholds_without_a_pair():
    sheet = read(metrics_excel(*ROWS))

    assert sheet.prepared.data["limits"] == [
        ["Cyclomatic Complexity", "Pass range 1 ~ 10"],
        ["Number of Call Levels (Deepest Nesting)", "Pass range 0~3"],
        ["Number of Calling Functions (Max. Number of Calling Functions)", "Pass range 0 ~ 4"],
        ["Number of Called Functions", "Threshold 6"],
    ]


def test_kind_groups_by_function_and_leaves_middle_rows_for_people():
    fail, middle = read(metrics_excel(*ROWS)).findings[:2]

    assert KINDS["codemetrics"] is CODEMETRICS
    assert CODEMETRICS.group_key(fail, "src/grade.c") == "src/grade.c:grade"
    assert (CODEMETRICS.label(fail), CODEMETRICS.label(middle)) == (FAIL, MIDDLE)
    assert (CODEMETRICS.auto_select(fail), CODEMETRICS.auto_select(middle)) == (True, False)
    assert CODEMETRICS.rank(FAIL) < CODEMETRICS.rank(MIDDLE) < CODEMETRICS.rank(UNKNOWN)


def test_condition_rows_fixture_is_read_as_three_metrics():
    assert [m.name for m in condition_metrics(CONDITION_ROWS)] == [
        "Cyclomatic Complexity",
        "Deepest Nesting",
        "Max. Number of Calling Functions",
    ]


def test_calling_and_called_metrics_pair_with_their_own_check():
    # 부르는 곳 수와 부르는 함수 수가 둘 다 있을 때 서로 바뀌어 이어지면 단계가 뒤집힌다
    conditions = [
        ["No", "지표", "기준", "판정"],
        [1, "Max. Number of Called Functions", "0 ~ 6", "Pass"],
        [None, None, "> 6", "Fail"],
        [2, "Max. Number of Calling Functions", "0 ~ 4", "Pass"],
        [None, None, "> 4", "Fail"],
    ]
    rows = [
        metric_row("Number of Calling Functions", threshold=4, actual=5),
        metric_row("Number of Called Functions", threshold=6, actual=5),
    ]

    sheet = read(metrics_excel(*rows, conditions=conditions))

    assert sheet.prepared.data["pairs"] == {
        "number of calling functions": 1,
        "number of called functions": 0,
    }
    assert [(f.check, f.category) for f in sheet.findings] == [
        ("Number of Calling Functions", FAIL)
    ]
    assert sheet.skipped == {PASSED: 1}


def test_a_pass_with_conditions_is_a_middle_band_not_a_pass():
    # Conditional Pass 단계 판정 글에 pass 낱말이 섞여 있어도 Pass로 보고 빼면 그 단계가 통째로 빠진다
    conditions = [
        ["No", "지표", "기준", "판정"],
        [1, "Cyclomatic Complexity", "1 ~ 10", "Pass"],
        [None, None, "11 ~ 20", "Pass (검토 뒤 사유 기록)"],
        [None, None, ">= 21", "Fail"],
    ]

    sheet = read(metrics_excel(metric_row(actual=15), metric_row(actual=5), conditions=conditions))

    assert [(f.actual, f.category) for f in sheet.findings] == [("15", MIDDLE)]
    assert sheet.skipped == {PASSED: 1}
