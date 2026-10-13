from llm_lab.csource import (
    MAX_DEFINITION_LINES,
    Definition,
    blank_comments,
    find_definitions,
    split_lines,
    top_level_blocks,
)

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


HEADER = """\
#ifndef LIMITS_H
#define LIMITS_H
#define BUF_LEN 16
#define MIN(a, b) ((a) < (b) ? \\
    (a) : (b))
typedef unsigned char u8;
typedef int pair_t[2];
typedef void (*handler_t)(int);
typedef struct point_tag {
    int x;
    int y;
} point_t, *point_p;
enum { SLOT_A, SLOT_B = 4, SLOT_COUNT };
struct bare_tag { int z; };
extern u8 shared_buf[BUF_LEN];
int read_value(int index);
#endif
"""

SOURCE = """\
#include "limits.h"
/* 전역 표 */
static const u8 lookup[3] = {
    1, 2, 3
};
int counter = 0;
__attribute__((noinline)) static int helper(int a,
                                             int b)
{
    return a + b; /* { */
}
char *name_of(int id)
{
    return "x";
}
"""


def names(definitions: list[Definition]) -> list[tuple[str, str, int, int]]:
    return [(d.name, d.kind, d.first, d.last) for d in definitions]


def test_find_definitions_in_a_header():
    found = names(find_definitions("inc/limits.h", HEADER))

    assert ("LIMITS_H", "macro", 2, 2) in found
    assert ("BUF_LEN", "macro", 3, 3) in found
    assert ("MIN", "macro", 4, 5) in found  # 줄 이음은 다음 줄까지
    assert ("u8", "type", 6, 6) in found
    assert ("pair_t", "type", 7, 7) in found
    assert ("handler_t", "type", 8, 8) in found
    assert ("point_tag", "type", 9, 12) in found
    assert ("point_t", "type", 9, 12) in found and ("point_p", "type", 9, 12) in found
    assert ("SLOT_A", "type", 13, 13) in found and ("SLOT_COUNT", "type", 13, 13) in found
    assert ("bare_tag", "type", 14, 14) in found
    assert ("shared_buf", "variable", 15, 15) in found
    assert not any(name == "read_value" for name, _, _, _ in found)  # 원형은 정의가 아니다


def test_find_definitions_in_a_source_file():
    found = names(find_definitions("src/a.c", SOURCE))

    assert ("lookup", "variable", 3, 5) in found
    assert ("counter", "variable", 6, 6) in found
    assert ("helper", "function", 7, 11) in found  # 서명이 두 줄, 속성은 무시
    assert ("name_of", "function", 12, 15) in found
    assert not any(name in ("include", "a", "b") for name, _, _, _ in found)


def test_definition_text_is_the_original_lines_without_numbers():
    (lookup,) = [d for d in find_definitions("src/a.c", SOURCE) if d.name == "lookup"]

    assert lookup.file == "src/a.c"
    assert lookup.text == "static const u8 lookup[3] = {\n    1, 2, 3\n};"


def test_long_definition_is_clipped():
    body = "\n".join(f"    {i}," for i in range(200))
    text = f"const int big[] = {{\n{body}\n}};\n"

    (big,) = find_definitions("t.c", text)

    lines = big.text.split("\n")
    assert len(lines) == MAX_DEFINITION_LINES + 1
    assert lines[-1] == f"... ({202 - MAX_DEFINITION_LINES} more lines)"  # 머리 1줄 + 200줄 + '};'
    assert (big.first, big.last) == (1, 202)


def test_same_name_at_the_same_place_is_recorded_once():
    text = "typedef struct node { int v; } node;\n"

    assert names(find_definitions("n.h", text)) == [("node", "type", 1, 1)]
