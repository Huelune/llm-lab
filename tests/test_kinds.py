from rte_fixtures import RTE_HEADER, finding, rte_row

from llm_lab.kinds import KINDS, RTE
from llm_lab.polyspace import Finding, column_name


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


def test_kinds_have_unique_names_and_defaults_among_choices():
    assert all(name == kind.name for name, kind in KINDS.items())
    for kind in KINDS.values():
        assert set(kind.defaults) <= set(kind.choices)
