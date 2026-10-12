from llm_lab.cfunction import (
    FunctionRange,
    braces_balanced,
    function_name,
    function_range,
    signature_text,
    squash_code,
)

ALLMAN = """\
#include "a.h"

/* 들어온 값을 고른다.
   가운데 줄이 '*'로 시작하지 않는 블록 주석 */
static int
pick(int a, int b)
{
    if (a > b) { return a; } /* '{' in comment */
    return b;
}
"""

KR = """\
int counter;
// 더한다
int add(int a, int b) {
    const char *s = "}{";
    return a + b + (s[0] == '}');
}
"""


def test_finds_signature_above_brace_and_the_whole_leading_comment():
    found = function_range(ALLMAN, 6, "pick")

    assert found == FunctionRange(start=3, signature=5, open_line=7, end=10)


def test_line_inside_the_body_or_on_the_brace_finds_the_same_function():
    assert function_range(ALLMAN, 8, "pick") == function_range(ALLMAN, 7, "pick")


def test_brace_on_the_signature_line_and_braces_in_strings():
    assert function_range(KR, 3, "add") == FunctionRange(start=2, signature=3, open_line=3, end=6)


def test_comment_split_by_a_blank_line_is_not_part_of_the_function():
    text = "/* file header */\n\nint f(void)\n{\n    return 0;\n}\n"

    assert function_range(text, 3, "f") == FunctionRange(start=3, signature=3, open_line=4, end=6)


def test_same_name_twice_picks_the_definition_at_the_line():
    text = (
        "#if A\nstatic int g(void)\n{\n    return 1;\n}\n"
        "#else\nstatic int g(void)\n{\n    return 2;\n}\n#endif\n"
    )

    assert function_range(text, 7, "g") == FunctionRange(start=7, signature=7, open_line=8, end=10)


def test_other_name_or_no_block_is_none():
    assert function_range(ALLMAN, 6, "other") is None
    assert function_range("int x;\n", 1, "x") is None


def test_function_name_drops_parentheses_and_spaces():
    assert [function_name(t) for t in ("foo()", " foo ( void ) ", "foo", "")] == [
        "foo",
        "foo",
        "foo",
        "",
    ]


def test_signature_text_ignores_comments_and_layout():
    found = function_range(ALLMAN, 6, "pick")
    assert found is not None

    # 공백은 한 칸으로 줄이고, 기호 앞뒤 공백은 없앤다 (줄바꿈·띄어쓰기 차이를 같은 것으로 본다)
    assert signature_text(ALLMAN, found) == "static int pick(int a,int b)"
    assert signature_text(ALLMAN, found) in squash_code("static int  pick( int a,\n int b) /* x */")
    assert signature_text(ALLMAN, found) not in squash_code("static int pick(int a, long b)")


def test_braces_balanced_skips_comments_strings_and_characters():
    assert braces_balanced("int f(void)\n{\n    /* } */ return '{' + \"}\"[0];\n}\n")
    assert not braces_balanced("int f(void)\n{\n    if (x) {\n}\n")
    assert not braces_balanced("}{")
