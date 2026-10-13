from llm_lab.csource import (
    MAX_DEFINITION_LINES,
    Definition,
    blank_comments,
    find_definitions,
    identifiers,
    scan_folder,
    split_lines,
    top_level_blocks,
    used_definitions,
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


STRUCT_USERS = """struct point make_point(void)
{
    struct point p = {0, 0};
    return p;
}
struct node *find_node(int key)
{
    return 0;
}
enum_t get_mode(void)
{
    return 0;
}
struct cfg g_cfg = {1, 2};
struct cfg g_other;
enum mode current_mode;
struct fwd;
static int mask = (1 << 3);
uint8_t flag = (uint8_t)1;
"""


def test_struct_and_enum_used_as_types_do_not_make_a_type_definition():
    found = names(find_definitions("s.c", STRUCT_USERS))

    assert ("make_point", "function", 1, 5) in found  # struct를 돌려주는 함수
    assert ("find_node", "function", 6, 9) in found
    assert ("get_mode", "function", 10, 13) in found  # enum_t는 enum 키워드가 아니다
    assert ("g_cfg", "variable", 14, 14) in found  # 초기화 있는 struct 전역
    assert ("g_other", "variable", 15, 15) in found  # 초기화 없는 struct 전역
    assert ("current_mode", "variable", 16, 16) in found
    assert not any(kind == "type" for _, kind, _, _ in found)
    assert not any(name == "fwd" for name, _, _, _ in found)  # 전방 선언만 뺀다


def test_initialized_variable_with_parentheses_is_a_variable():
    found = names(find_definitions("s.c", STRUCT_USERS))

    assert ("mask", "variable", 18, 18) in found
    assert ("flag", "variable", 19, 19) in found


def test_scan_folder_reads_c_and_h_in_any_case_and_counts_unreadable(tmp_path):
    (tmp_path / "inc").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "inc" / "LIMITS.H").write_bytes(b"#define BUF_LEN 16\n")
    (tmp_path / "main.c").write_bytes("int counter; /* 주석 */\n".encode("cp949"))
    (tmp_path / "mixed.c").write_bytes(b"int a;\r\nint b;\n")  # 줄바꿈이 섞여 못 읽음
    (tmp_path / "notes.txt").write_bytes(b"int ignored;\n")
    (tmp_path / ".git" / "hook.c").write_bytes(b"int hidden;\n")

    definitions, unreadable = scan_folder(tmp_path)

    # 훑는 순서는 폴더 구조를 따르므로 정렬해서 비교한다
    assert sorted((d.name, d.file) for d in definitions) == [
        ("BUF_LEN", "inc/LIMITS.H"),
        ("counter", "main.c"),
    ]
    assert unreadable == 1


def test_identifiers_are_unique_in_order_without_keywords_or_comment_text():
    text = "int total = BUF_LEN + lookup[0]; // BUF_LEN again, hidden_name\nreturn total;\n"

    assert identifiers(text) == ["total", "BUF_LEN", "lookup"]


def test_used_definitions_keeps_only_names_the_analysed_sources_use():
    definitions = [
        Definition("BUF_LEN", "macro", "inc/limits.h", 1, 1, "#define BUF_LEN 16"),
        Definition("unused_fn", "function", "b.c", 1, 3, "int unused_fn(void)\n{\n}"),
        Definition("counter", "variable", "a.c", 6, 6, "int counter = 0;"),
    ]

    kept = used_definitions(definitions, ["int x = BUF_LEN;\n", "counter++;\n"])

    assert [d.name for d in kept] == ["BUF_LEN", "counter"]
