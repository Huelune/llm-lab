import pytest
from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_finding, misra_row, open_workbook
from rte_fixtures import RTE_HEADER, finding, rte_row, workbook_bytes

from llm_lab.fixer import select_region
from llm_lab.kinds import CODEMETRICS, KINDS, MISRA, RTE
from llm_lab.kinds.misra import NO_CATEGORY, category_word, rule_key
from llm_lab.polyspace import Finding, Prepared, column_name

# 화면에 나가는 글. 코드의 상수와 따로 적어 두어, 글이 바뀌면 알아챈다
NO_RULE_NUMBER_NOTE = "규칙 번호를 못 읽음: 결과 시트 분류를 씀"
NOT_ON_SHEET_NOTE = "조건 시트에서 못 찾은 규칙: 결과 시트 분류를 씀"
NO_USABLE_RULES_NOTE = "조건 시트에서 쓸 규칙이 없음: 결과 시트 분류를 씀"


def cells(header: list, values: list):
    """read_row에 넘기는 칸 읽기 함수: 열 이름 → 그 행의 값 (없는 열이면 None)."""
    row = dict(zip([column_name(name) for name in header], values, strict=True))
    return row.get


def test_rte_reads_red_and_orange_rows_and_names_other_types():
    red = RTE.read_row(cells(RTE_HEADER, rte_row("Red  check", line=7)), 5, None)

    assert isinstance(red, Finding)
    assert (red.row, red.color, red.type, red.line) == (5, "Red", "Red  check", 7)
    assert RTE.read_row(cells(RTE_HEADER, rte_row("Gray Check")), 6, None) == "Gray Check"
    assert RTE.read_row(cells(RTE_HEADER, rte_row(None)), 7, None) == "(빈 TYPE)"


def test_rte_priority_and_colors():
    assert RTE.priority(finding(color="Red")) < RTE.priority(finding(color="Orange"))
    assert RTE.rank("기타") == len(RTE.choices)  # choices에 없는 글자는 맨 뒤
    assert (RTE.color("Red"), RTE.color("Orange"), RTE.color("기타")) == ("red", "orange", "gray")


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("10.3 The value of an expression", "10.3"),
        ("Rule 10.3", "10.3"),
        ("rule10.3", "10.3"),
        (" 1.1 ", "1.1"),
        ("Dir 4.1 Run-time failures", "D4.1"),
        ("D4.1", "D4.1"),
        ("Directive 4.1", "D4.1"),
        ("See rule 10.3", None),  # 맨 앞에 번호가 없다
        ("8 bits", None),  # 점으로 이은 숫자가 아니다
        ("", None),
    ],
)
def test_rule_key_takes_the_leading_rule_number(text, key):
    assert rule_key(text) == key


def test_rule_key_marks_directives_named_by_group():
    assert rule_key("4.1 Run-time failures shall be minimized", directive=True) == "D4.1"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Category: Required", "Required"),
        ("category:MANDATORY", "Mandatory"),
        ("advisory", "Advisory"),
        ("Category: -", ""),
        ("", ""),
    ],
)
def test_category_word(text, category):
    assert category_word(text) == category


def test_condition_sheet_is_found_by_its_columns_and_read_loosely():
    data = workbook_bytes(
        {
            "MISRA_Result": [MISRA_HEADER],
            "Notes": [["Guideline", "Remarks"]],  # mode 열이 없으면 조건 시트가 아니다
            "Rules": CONDITION_ROWS,
        }
    )

    prepared = MISRA.prepare(open_workbook(data))

    assert prepared.data == {
        "10.4": ["Required", True],
        "12.1": ["Advisory", True],
        "2.2": ["Advisory", False],
        "D4.6": ["Mandatory", True],
        "9.1": ["Disapplied", True],
    }
    assert prepared.note == "조건 시트: 규칙 5개"


def test_condition_sheet_named_misra_wins_and_result_sheets_are_never_conditions():
    data = workbook_bytes(
        {
            "Other_Result": [["Guideline", "Mode"], ["10.4", "required"]],
            "Rules": [["Guideline", "Mode"], ["10.4", "advisory"]],
            "MISRA rules": [["Guideline", "Mode"], ["10.4", "mandatory"]],
        }
    )

    assert MISRA.prepare(open_workbook(data)).data == {"10.4": ["Mandatory", True]}


def test_rule_numbers_that_collide_are_not_used():
    # 숫자 칸으로 저장된 8.10은 8.1로 읽혀 진짜 8.1과 겹친다
    rows = [["Guideline", "Mode"], [8.1, "required"], [8.10, "advisory"], [10.4, "mandatory"]]

    prepared = MISRA.prepare(open_workbook(workbook_bytes({"Rules": rows})))

    assert prepared.data == {"10.4": ["Mandatory", True]}
    assert prepared.note == "조건 시트: 규칙 1개 · 번호가 겹친 1개는 결과 시트 분류를 씀"


@pytest.mark.parametrize(
    ("mode", "category"),
    [
        ("REQUIRED", "Required"),
        ("  mandatory ", "Mandatory"),
        ("Not   required", "Not required"),  # 낱말이 들어 있어도 세 분류 이름과 같지 않다
        ("advisory only", "Advisory only"),
        ("disapplied", "Disapplied"),
    ],
)
def test_condition_mode_is_one_of_the_three_words_exactly_or_kept_as_written(mode, category):
    rows = [["Guideline", "Mode"], ["10.4", mode]]

    prepared = MISRA.prepare(open_workbook(workbook_bytes({"Rules": rows})))

    assert prepared.data == {"10.4": [category, True]}


def test_without_condition_sheet_the_result_sheet_categories_are_used():
    data = workbook_bytes({"MISRA_Result": [MISRA_HEADER], "Notes": [["Guideline", "Remarks"]]})

    assert MISRA.prepare(open_workbook(data)) == Prepared({}, "조건 시트 없음: 결과 시트 분류를 씀")


@pytest.mark.parametrize(
    "rows",
    [
        [["Guideline", "Mode"]],
        [["Guideline", "Mode"], ["see manual", "required"], [None, "advisory"]],
        [["Guideline", "Mode"], ["10.4", None], ["12.1", "  "]],
        [["Guideline", "Mode"], [8.1, "required"], [8.10, "advisory"]],
    ],
    ids=["header only", "no rule number", "blank mode", "every number repeated"],
)
def test_condition_sheet_without_a_usable_rule_is_not_reported_as_missing(rows):
    prepared = MISRA.prepare(open_workbook(workbook_bytes({"Rules": rows})))

    assert prepared == Prepared({}, NO_USABLE_RULES_NOTE)


CONDITIONS = {"10.4": ["Advisory", True], "2.2": ["Required", False]}


def misra(values: list, context=CONDITIONS):
    return MISRA.read_row(cells(MISRA_HEADER, values), 9, context)


def test_misra_row_takes_the_condition_category_and_notes_a_difference():
    found = misra(misra_row(category="Required"))

    assert isinstance(found, Finding)
    assert (found.row, found.color, found.line) == (9, "", 3)
    assert found.information == "Category: Required"
    assert (found.category, MISRA.label(found)) == ("Advisory", "Advisory")
    assert found.note == "분류: 조건 시트 Advisory (결과 시트 Required)"


def test_misra_row_of_a_disabled_rule_is_skipped():
    assert misra(misra_row("2.2 There shall be no dead code")) == "적용 안 함 (조건 시트)"


def test_misra_row_without_a_condition_uses_the_result_category():
    found = misra(misra_row("12.1 Precedence", category="Mandatory"))
    unknown = misra(misra_row("12.1 Precedence", category=""), context={})

    assert (found.category, found.note) == ("Mandatory", NOT_ON_SHEET_NOTE)
    assert MISRA.label(unknown) == NO_CATEGORY


def test_misra_row_without_a_rule_number_says_so_and_uses_the_result_category():
    found = misra(misra_row("See rule 10.3 for details", category="Advisory"))

    assert (found.category, found.note) == ("Advisory", NO_RULE_NUMBER_NOTE)


def test_misra_row_of_a_rule_number_dropped_as_repeated_is_not_on_the_sheet():
    rows = [["Guideline", "Mode"], [8.1, "required"], [8.10, "advisory"], [10.4, "mandatory"]]
    prepared = MISRA.prepare(open_workbook(workbook_bytes({"Rules": rows})))

    found = misra(misra_row("8.1 Declarations", category="Advisory"), prepared.data)

    assert (found.category, found.note) == ("Advisory", NOT_ON_SHEET_NOTE)


@pytest.mark.parametrize("context", [{}, None], ids=["no rules", "no context"])
@pytest.mark.parametrize("check", ["12.1 Precedence", "See rule 10.3 for details"])
def test_misra_row_keeps_an_empty_note_when_there_is_no_condition_sheet(check, context):
    found = misra(misra_row(check, category="Mandatory"), context=context)
    unknown = misra(misra_row(check, category=""), context=context)

    assert (found.category, found.note) == ("Mandatory", "")
    assert (MISRA.label(unknown), unknown.note) == (NO_CATEGORY, "")


def test_directive_rows_use_directive_conditions():
    context = {"4.1": ["Advisory", True], "D4.1": ["Required", False]}

    rule = misra(misra_row("4.1 Escape sequences", group="4 Character sets"), context)
    directive = misra(misra_row("4.1 Run-time failures", group="Directive 4 Code design"), context)

    assert rule.category == "Advisory"
    assert directive == "적용 안 함 (조건 시트)"


def test_preprocessing_directive_rules_are_still_rules():
    # 규칙 묶음 20의 이름 "Preprocessing directives"에는 directive가 들어 있지만 디렉티브가 아니다
    context = {"20.7": ["Mandatory", False], "20.5": ["Mandatory", True]}
    group = "20 Preprocessing directives"

    disabled = misra(misra_row("20.7 Macro parameters", group=group), context)
    found = misra(misra_row("20.5 Undefining macros", group=group, category="Advisory"), context)

    assert disabled == "적용 안 함 (조건 시트)"
    assert (found.category, found.note) == (
        "Mandatory",
        "분류: 조건 시트 Mandatory (결과 시트 Advisory)",
    )


def test_misra_groups_rows_on_the_same_line_of_the_same_file():
    first = misra_finding()
    same_line = misra_finding(row=3, check="12.1 Precedence")

    assert MISRA.group_key(first, "src/calc.c") == "src/calc.c:3"
    assert MISRA.group_key(same_line, "src/calc.c") == "src/calc.c:3"
    assert MISRA.group_key(misra_finding(line=4), "src/calc.c") == "src/calc.c:4"
    assert MISRA.group_key(first, "src/other.c") == "src/other.c:3"
    assert MISRA.group_key(misra_finding(line=None), "src/calc.c") == ""  # 혼자 보낸다


def test_rte_groups_rows_of_the_same_function_of_the_same_file():
    first = finding()
    same_function = finding(row=3, line=1, check="Overflow", function=" divide ")

    assert RTE.group_key(first, "src/calc.c") == "src/calc.c:divide"
    assert RTE.group_key(same_function, "src/calc.c") == "src/calc.c:divide"
    # C는 대소문자를 가리므로 이름을 소문자로 맞추지 않는다
    assert RTE.group_key(finding(function="Divide"), "src/calc.c") == "src/calc.c:Divide"
    assert RTE.group_key(finding(function="other"), "src/calc.c") == "src/calc.c:other"
    assert RTE.group_key(first, "src/other.c") == "src/other.c:divide"
    assert RTE.group_key(finding(function=""), "src/calc.c") == ""  # 혼자 보낸다


def test_rte_region_covers_every_row_of_the_group():
    # 2400줄: 네 줄짜리 함수 600개. 파일이 길면 행마다 그 함수만 고르는데, 묶음은 모두 덮어야 한다
    text = "".join(f"int f{i}(void)\n{{\n    return {i};\n}}\n" for i in range(600))
    top, bottom = finding(line=3), finding(row=3, line=2399)

    assert RTE.region(text, [top]) == select_region(text, 3)
    assert RTE.region(text, [top, bottom]) == (1, 2400)
    assert RTE.region(text, [bottom, top]) == (1, 2400)


def test_rte_and_misra_prompts_offer_the_manual_decision():
    # 답 처리가 바뀌었으므로(판단 값 추가) prompt_version도 올라간다
    assert "manual" in RTE.system_prompt and RTE.prompt_version == 5  # 가드 허용도 manual을 둔다
    assert "answer manual" in RTE.prompt({"mode": "root_cause"})
    assert "answer manual" in MISRA.system_prompt and MISRA.prompt_version == 2
    assert "manual" not in KINDS["codemetrics"].system_prompt  # CodeMetrics는 그대로


def test_misra_order_colors_and_defaults():
    assert MISRA.defaults == ("Mandatory", "Required")
    ranks = [MISRA.rank(label) for label in ("Mandatory", "Required", "Advisory", NO_CATEGORY)]
    assert ranks == [0, 1, 2, 3]
    assert (MISRA.color("Mandatory"), MISRA.color("Disapplied")) == ("red", "gray")


def test_misra_facts_send_the_category_instead_of_information():
    facts = dict(MISRA.facts(misra_finding(category="Advisory")))

    assert facts["category"] == "Advisory"
    assert "information" not in facts
    assert dict(MISRA.facts(misra_finding(category=NO_CATEGORY)))["category"] == ""


def test_kinds_have_unique_names_and_defaults_among_choices():
    assert all(name == kind.name for name, kind in KINDS.items())
    for kind in KINDS.values():
        assert set(kind.defaults) <= set(kind.choices)


def test_misra_prompt_sends_fixes_that_live_elsewhere_to_manual():
    assert (
        "If the fix belongs in a definition or a caller that is not shown, "
        "answer manual instead and say where."
    ) in MISRA.system_prompt


def test_rte_and_misra_prompts_explain_the_definitions_section():
    sentence = "Definitions of functions, macros, variables and types"
    assert sentence in RTE.system_prompt and sentence in MISRA.system_prompt
    assert "Edits must stay in the numbered code;" in MISRA.system_prompt
    assert "answer manual and name that file and function" in RTE.prompt({"mode": "root_cause"})
    assert "Definitions of" not in CODEMETRICS.system_prompt
    assert RTE.prompt_version == 5 and MISRA.prompt_version == 2  # 답 처리는 그대로


def test_rte_modes_default_to_guard_and_pick_the_prompt():
    # 작업마다 고르는 처리 방식. 첫 항목이 기본값이고, 작업 정보의 mode로 지시문을 고른다
    assert [value for value, _, _ in RTE.modes] == ["guard", "root_cause"]
    assert RTE.system_prompt == RTE.system_prompt_for({"mode": "guard"}) == RTE.prompt(None)
    assert "a guard is acceptable" in RTE.prompt({"mode": "guard"})
    root = RTE.prompt({"mode": "root_cause"})
    assert "Never make such a change" in root and "a guard is acceptable" not in root
    assert "answer manual and name that file and function" in root
    assert MISRA.modes == () and MISRA.prompt({"anything": 1}) == MISRA.system_prompt


def test_rte_prompts_let_the_model_fix_editable_definitions():
    for mode in ("guard", "root_cause"):
        prompt = RTE.prompt({"mode": mode})
        assert "Those marked editable may be changed" in prompt
        assert 'set "file" to that path' in prompt
        assert "A change there affects every caller" in prompt
        assert 'Set "file" to "" for edits in the numbered code' in prompt
    assert "guard the operation here and name that file and function" in RTE.prompt(
        {"mode": "guard"}
    )
    root = RTE.prompt({"mode": "root_cause"})
    assert "answer manual and name that file and function" in root
    assert (
        "the cause is in a caller that is not shown or in a definition marked reference only"
        in root
    )
    assert "editable" not in MISRA.system_prompt


def test_rte_prompts_explain_callers():
    for mode in ("guard", "root_cause"):
        prompt = RTE.prompt({"mode": mode})
        assert "Callers of the function may follow" in prompt
        assert "which values really reach its parameters" in prompt
        assert (
            "If callers are shown, every one guarantees safety and none are said to be not shown"
            in prompt
        )
        assert "you may fix it in that caller, where the value is produced" in prompt
    guard = RTE.prompt({"mode": "guard"})
    assert "changing an editable caller or definition would be risky" in guard
    root = RTE.prompt({"mode": "root_cause"})
    assert "If the cause is in a caller that is not shown" in root
    assert "Callers of" not in MISRA.system_prompt
