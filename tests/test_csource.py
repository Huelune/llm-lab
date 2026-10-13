from llm_lab.csource import blank_comments, split_lines, top_level_blocks

CODE = """\
/* { 주석 속 중괄호 */
int add(int a, int b) // {
{
    char c = '{';
    const char *s = "}";
    return a + b;
}
static int table[2] = {1, 2};
"""


def test_blank_comments_keeps_line_count_and_column_positions():
    blanked = blank_comments(CODE)

    assert blanked.count("\n") == CODE.count("\n")
    assert [len(line) for line in split_lines(blanked)] == [len(line) for line in split_lines(CODE)]
    assert "주석" not in blanked and "'{'" not in blanked and '"}"' not in blanked
    assert "int add(int a, int b)" in blanked


def test_blank_comments_keeps_newline_escaped_inside_string():
    text = 'const char *s = "a\\\nb";\nint x;\n'

    assert blank_comments(text).count("\n") == text.count("\n")


def test_top_level_blocks_ignores_braces_in_comments_strings_and_chars():
    assert top_level_blocks(CODE) == [(3, 7), (8, 8)]


def test_split_lines_drops_only_the_trailing_empty_piece():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("a\n\n") == ["a", ""]
