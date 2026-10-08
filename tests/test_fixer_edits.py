import pytest

from llm_lab import fixer
from llm_lab.fixer import (
    Edit,
    EditMismatch,
    FixResult,
    apply_edits,
    display_diff,
    locate_edits,
    numbered,
    select_region,
    split_lines,
)

CODE = """int add(int a, int b)
{
    return a + b;
}
"""


def long_file(function_at: int, body_lines: int = 3) -> str:
    """function_at번째 줄 앞까지 선언을 채우고 그 뒤에 함수를 둔 긴 파일."""
    lines = [f"int g{n};" for n in range(1, function_at)]
    lines += ["/* { not a block } */", "static int", "calc(int a,", "     int b)", "{"]
    lines += [f'    a += {n}; /* "}}" */' for n in range(body_lines)]
    lines += ["    return a;", "}"]
    # 파일 전체를 보내는 기준보다 항상 길게 만든다
    lines += [f"int h{n};" for n in range(fixer.WHOLE_FILE_MAX_LINES)]
    return "\n".join(lines) + "\n"


def test_split_lines_only_splits_on_newline():
    assert split_lines("a\fb\nc\n") == ["a\fb", "c"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("") == []


def test_small_file_is_sent_whole():
    assert select_region(CODE, 3) == (1, 4)


def test_files_up_to_two_thousand_lines_are_sent_whole():
    # 2,000줄(대략 2만~3만 토큰)까지는 함수만 자르지 않고 파일 전체를 보낸다
    text = "".join(f"int g{n};\n" for n in range(1500))

    assert select_region(text, 700) == (1, 1500)


def test_long_file_sends_enclosing_function_with_signature_and_comment_above():
    text = long_file(function_at=450)
    lines = split_lines(text)
    brace = lines.index("{") + 1

    first, last = select_region(text, brace + 2)

    # 시그니처 4줄과 바로 위 주석까지, 그 위의 "int g449;"(';'로 끝남)에서 멈춘다
    assert lines[first - 1] == "/* { not a block } */"
    assert lines[first - 2] == "int g449;"
    assert lines[last - 1] == "}"
    assert last - first == 4 + 1 + 3 + 1


def test_braces_in_comments_and_strings_are_ignored():
    text = 'char *s = "{";\n/* { */\nint f(void)\n{\n    return 1; // }\n}\n'

    assert fixer._top_level_blocks(text) == [(4, 6)]


def test_falls_back_to_window_outside_any_function(monkeypatch):
    monkeypatch.setattr(fixer, "WHOLE_FILE_MAX_LINES", 10)
    text = "".join(f"int g{n};\n" for n in range(200))

    assert select_region(text, 100) == (40, 160)
    assert select_region(text, 5) == (1, 65)


def test_too_long_function_falls_back_to_window(monkeypatch):
    monkeypatch.setattr(fixer, "WHOLE_FILE_MAX_LINES", 10)
    text = "int f(void)\n{\n" + "    x++;\n" * 300 + "}\n"

    assert select_region(text, 150) == (90, 210)


def test_numbered_prefixes_original_line_numbers():
    assert numbered(CODE, 2, 3) == "    2| {\n    3|     return a + b;"


def test_locate_exact_match():
    edits = locate_edits(
        CODE, 1, 4, [{"original": "return a + b;", "replacement": "return b + a;"}]
    )

    assert apply_edits(CODE, edits) == CODE.replace("a + b", "b + a")


def test_locate_ignores_whitespace_differences_line_by_line():
    raw = [{"original": "  return   a + b;", "replacement": "\treturn safe_add(a, b);\n"}]

    edits = locate_edits(CODE, 1, 4, raw)

    assert apply_edits(CODE, edits) == CODE.replace("    return a + b;", "\treturn safe_add(a, b);")


def test_locate_keeps_line_breaks_when_replacement_drops_trailing_newline():
    raw = [{"original": "    return a + b;\n", "replacement": "    return 0;"}]

    assert apply_edits(CODE, locate_edits(CODE, 1, 4, raw)) == CODE.replace("a + b", "0")


def test_locate_whitespace_match_on_last_line_without_newline():
    text = "int f(void)\n{\n    return 1; }"

    edits = locate_edits(text, 1, 3, [{"original": "return 1;  }", "replacement": "return 2; }"}])

    assert apply_edits(text, edits) == "int f(void)\n{\nreturn 2; }"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([{"original": "missing();", "replacement": "x"}], "찾을 수 없음"),
        ([{"original": "a", "replacement": "x"}], "번 나옴"),
        ([{"original": "  ", "replacement": "x"}], "비어 있음"),
        (
            [
                {"original": "return a + b;", "replacement": "x"},
                {"original": "a + b", "replacement": "y"},
            ],
            "같은 줄",
        ),
    ],
)
def test_locate_rejects_unusable_edits(raw, message):
    with pytest.raises(EditMismatch, match=message):
        locate_edits(CODE, 1, 4, raw)


def test_locate_strips_copied_line_number_prefixes():
    raw = [{"original": "    3|     return a + b;", "replacement": "    3|     return b + a;"}]

    edits = locate_edits(CODE, 1, 4, raw)

    assert apply_edits(CODE, edits) == CODE.replace("a + b", "b + a")


def test_locate_strips_prefixes_when_only_some_replacement_lines_have_them():
    # 원본 줄은 번호째 복사하고 새로 쓴 줄에는 번호를 안 붙인 경우
    raw = [
        {
            "original": "    2| {\n    3|     return a + b;",
            "replacement": "    2| {\n    if (a > 0) {}\n    3|     return b + a;",
        }
    ]

    edits = locate_edits(CODE, 1, 4, raw)

    assert apply_edits(CODE, edits) == CODE.replace(
        "    return a + b;", "    if (a > 0) {}\n    return b + a;"
    )


def test_locate_strips_prefix_from_some_original_lines():
    raw = [{"original": "    2| {\n    return a + b;", "replacement": "{\n    return b + a;"}]

    edits = locate_edits(CODE, 1, 4, raw)

    assert apply_edits(CODE, edits) == CODE.replace("a + b", "b + a")


def test_locate_only_searches_sent_region():
    text = "int x = 1;\nint f(void)\n{\n    int x = 1;\n}\n"

    edits = locate_edits(text, 3, 5, [{"original": "int x = 1;", "replacement": "int x = 2;"}])

    assert apply_edits(text, edits) == "int x = 1;\nint f(void)\n{\n    int x = 2;\n}\n"


def test_display_diff_marks_changed_lines():
    diff = display_diff("calc.c", CODE, CODE.replace("a + b", "b + a"))

    assert diff.split("\n") == [
        "--- a/calc.c",
        "+++ b/calc.c",
        "@@ -1,4 +1,4 @@",
        " int add(int a, int b)",
        " {",
        "-    return a + b;",
        "+    return b + a;",
        " }",
    ]


def test_fix_result_round_trips_through_json():
    result = FixResult("fix", "범위 검사 추가", (Edit(1, 5, "x\n"),), "diff")

    assert FixResult.from_json(result.to_json()) == result
