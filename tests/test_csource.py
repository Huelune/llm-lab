from llm_lab import csource
from llm_lab.csource import (
    CALLER_CONTEXT,
    MAX_CALLER_LINES,
    MAX_CALLERS,
    MAX_DEFINITION_LINES,
    MAX_DEFINITION_TOTAL_LINES,
    MAX_DEFINITIONS,
    MAX_STORED_CALLERS,
    Definition,
    blank_comments,
    callers_for,
    enclosing_function,
    find_call_sites,
    find_definitions,
    function_blocks,
    identifiers,
    macro_target,
    pick_callers,
    pick_definitions,
    scan_folder,
    scan_project,
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


def definition(name, file="inc/limits.h", first=1, kind="macro", lines=1):
    text = "\n".join(f"{name} line {i}" for i in range(lines))
    return Definition(name, kind, file, first, first + lines - 1, text)


REGION = """\
#include "limits.h"
static int local_helper(void)
{
    return 1;
}
int use(int i)
{
    int v = lookup[i % BUF_LEN];
    int w = v;
    int x = w;
    int y = x;
    return y + local_helper() + counter;
}
"""


def test_pick_prefers_names_near_the_finding_then_the_rest_in_order():
    definitions = [
        definition("counter", file="a.c", kind="variable"),
        definition("BUF_LEN"),
        definition("lookup", file="a.c", first=5, kind="variable"),
    ]

    # 검사 줄 12의 위아래 2줄(10~13)에는 counter만 있고, lookup·BUF_LEN은 8줄에 있다
    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [12])

    assert [d.name for d in picked] == ["counter", "lookup", "BUF_LEN"]


def test_pick_skips_definitions_inside_the_code_being_sent():
    inside = Definition("local_helper", "function", "src/use.c", 2, 5, "...")
    outside = Definition("local_helper", "function", "other.c", 2, 5, "...")

    picked = pick_definitions([inside, outside], "src/use.c", REGION, 1, 13, [12])

    assert picked == [outside]


def test_pick_ranks_same_file_then_headers_and_keeps_two_per_name():
    definitions = [
        definition("BUF_LEN", file="z.c"),
        definition("BUF_LEN", file="inc/limits.h"),
        definition("BUF_LEN", file="src/use.c", first=90),
        definition("BUF_LEN", file="inc/other.h"),
    ]

    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [8])

    assert [(d.file, d.first) for d in picked] == [("src/use.c", 90), ("inc/limits.h", 1)]


def test_pick_keeps_both_ifdef_variants_from_one_header():
    definitions = [definition("BUF_LEN", first=3), definition("BUF_LEN", first=7)]

    picked = pick_definitions(definitions, "src/use.c", REGION, 6, 13, [8])

    assert [d.first for d in picked] == [3, 7]


def test_pick_window_does_not_leave_the_sent_range():
    definitions = [definition("counter", file="a.c", kind="variable")]

    # 검사 줄 12의 창(10~14)이 범위 끝 13을 넘고, 검사 줄 8의 창(6~10)이 범위 시작 8 앞으로 나간다
    assert [d.name for d in pick_definitions(definitions, "src/use.c", REGION, 8, 13, [12])] == [
        "counter"
    ]
    assert [d.name for d in pick_definitions(definitions, "src/use.c", REGION, 8, 13, [8])] == [
        "counter"
    ]


def test_pick_stops_at_the_count_and_line_caps():
    many = [definition(f"name_{i}", first=i + 1) for i in range(MAX_DEFINITIONS + 3)]
    text = (
        "int f(void) { return "
        + " + ".join(f"name_{i}" for i in range(MAX_DEFINITIONS + 3))
        + "; }\n"
    )

    assert len(pick_definitions(many, "f.c", text, 1, 1, [1])) == MAX_DEFINITIONS

    big = [definition("huge", lines=MAX_DEFINITION_TOTAL_LINES - 5), definition("more", lines=10)]
    text = "int f(void) { return huge + more; }\n"

    assert [d.name for d in pick_definitions(big, "f.c", text, 1, 1, [1])] == ["huge"]


def test_pick_is_deterministic():
    definitions = [definition("lookup", file="a.c", kind="variable"), definition("BUF_LEN")]

    first = pick_definitions(definitions, "src/use.c", REGION, 6, 10, [8])
    second = pick_definitions(list(reversed(definitions)), "src/use.c", REGION, 6, 10, [8])

    assert first == second


MODE_ENUM = """\
typedef enum {
    MODE_A,
    MODE_B,
    MODE_C,
    MODE_D
} mode_t;
"""


def test_pick_sends_an_enum_once_however_many_of_its_names_are_used():
    definitions = find_definitions("inc/mode.h", MODE_ENUM)
    text = "int f(int x)\n{\n    switch (x) {\n    case MODE_A: return 1;\n"
    text += "    case MODE_B: return 2;\n    case MODE_C: return 3;\n    }\n    return 0;\n}\n"

    picked = pick_definitions(definitions, "src/use.c", text, 1, 9, [5])

    assert [(d.file, d.first, d.last) for d in picked] == [("inc/mode.h", 1, 6)]


def test_pick_sends_a_struct_once_when_its_tag_and_alias_are_both_used():
    header = "typedef struct node_tag { int v; } node_t, *node_p;\n"
    definitions = find_definitions("inc/node.h", header)
    text = "int f(struct node_tag *a, node_t *b, node_p c)\n{\n    return a->v;\n}\n"

    picked = pick_definitions(definitions, "src/use.c", text, 1, 4, [3])

    assert [(d.file, d.first, d.last) for d in picked] == [("inc/node.h", 1, 1)]


def test_repeated_names_of_one_body_count_once_toward_the_caps():
    enum = [Definition(f"E_{i}", "type", "e.h", 1, 3, "enum { E_0, E_1 };") for i in range(5)]
    others = [definition(f"name_{i}", first=10 + i) for i in range(MAX_DEFINITIONS)]
    text = "int f(void) { return " + " + ".join(d.name for d in [*enum, *others]) + "; }\n"

    picked = pick_definitions([*enum, *others], "f.c", text, 1, 1, [1])

    assert len(picked) == MAX_DEFINITIONS  # 열거 몸체 1개 + 나머지 11개
    assert [d.name for d in picked].count("E_0") == 1
    assert not any(d.name in ("E_1", "E_4") for d in picked)


CPLUSPLUS_HEADER = """\
#ifndef API_H
#define API_H
#ifdef __cplusplus
extern "C" {
#endif
#define API_MAX 8
typedef int api_id_t;
typedef enum {
    API_OK,
    API_FAIL
} api_status_t;
extern int api_table[API_MAX];
static inline int api_twice(int v)
{
    return v * 2;
}
#ifdef __cplusplus
}
#endif
#endif
"""


def test_header_wrapped_in_extern_c_is_scanned_inside_the_wrapper():
    found = names(find_definitions("inc/api.h", CPLUSPLUS_HEADER))

    assert ("API_MAX", "macro", 6, 6) in found
    assert ("api_id_t", "type", 7, 7) in found
    assert ("api_status_t", "type", 8, 11) in found
    assert ("API_OK", "type", 8, 11) in found
    assert ("api_table", "variable", 12, 12) in found
    assert ("api_twice", "function", 13, 16) in found
    assert not any(name == "extern" for name, _, _, _ in found)


def test_extern_c_braces_do_not_make_a_block():
    assert top_level_blocks(CPLUSPLUS_HEADER) == [(8, 11), (14, 16)]


MACRO_STYLE = """\
FUNC(void, CODE) Drv_Init(void)
{
    return;
}
VAR(uint8, AUTOMATIC) g_val = 0;
extern VAR(uint8, AUTOMATIC) g_other;
FUNC(void, CODE) Drv_Stop(void);
int read_value(int index);
int (*get_h(int id))(void)
{
    return 0;
}
"""


def test_macro_style_signatures_use_the_name_nearest_the_body():
    found = names(find_definitions("drv.c", MACRO_STYLE))

    assert ("Drv_Init", "function", 1, 4) in found  # FUNC(...)는 서명의 일부일 뿐이다
    assert ("g_val", "variable", 5, 5) in found  # VAR(...) 전역은 원형이 아니다
    assert ("g_other", "variable", 6, 6) in found
    assert ("get_h", "function", 9, 12) in found  # 함수 포인터를 돌려주는 함수
    assert not any(name in ("FUNC", "VAR", "Drv_Stop", "read_value") for name, _, _, _ in found)


def test_function_pointer_parameter_type_does_not_rename_the_function():
    text = "int run_job(unsigned x, handler_t (*cb)(int))\n{\n    return 0;\n}\n"

    assert names(find_definitions("run.c", text)) == [("run_job", "function", 1, 4)]


def test_pick_ignores_a_finding_line_far_before_the_sent_range():
    definitions = [
        definition("counter", file="a.c", kind="variable"),
        definition("lookup", file="a.c", first=5, kind="variable"),
    ]

    # 검사 줄 1은 범위 8~13보다 한참 앞이라 창이 없다. 줄 12의 창(counter)이 먼저 와야 한다
    picked = pick_definitions(definitions, "src/use.c", REGION, 8, 13, [1, 12])

    assert [d.name for d in picked] == ["counter", "lookup"]


def macro(name, text, file="inc/api.h", first=1):
    return Definition(name, "macro", file, first, first, text)


def function(name, file="lib/impl.c", first=10):
    return Definition(name, "function", file, first, first + 2, f"int {name}(int x)\n{{\n}}")


def test_macro_target_reads_simple_aliases_only():
    assert macro_target(macro("api_read", "#define api_read drv_read")) == "drv_read"
    assert macro_target(macro("api_read", "#define api_read(x) drv_read(x)")) == "drv_read"
    assert macro_target(macro("api_read", "#define api_read(x) (drv_read((x)))")) == "drv_read"
    assert macro_target(macro("BUF_LEN", "#define BUF_LEN 16")) is None
    assert macro_target(macro("SUM", "#define SUM(a, b) ((a) + (b))")) is None
    assert macro_target(macro("SELF", "#define SELF SELF")) is None
    assert macro_target(function("drv_read")) is None


def test_used_definitions_follow_macro_aliases_to_their_implementation():
    definitions = [
        macro("api_read", "#define api_read(x) mid_read(x)"),
        macro("mid_read", "#define mid_read drv_read", first=2),
        function("drv_read"),
        function("unrelated"),
    ]

    kept = used_definitions(definitions, ["int v = api_read(3);\n"])

    assert [d.name for d in kept] == ["api_read", "drv_read", "mid_read"]


def test_alias_cycles_stop():
    definitions = [macro("ping", "#define ping pong"), macro("pong", "#define pong ping", first=2)]

    assert [d.name for d in used_definitions(definitions, ["ping();\n"])] == ["ping", "pong"]
    picked = pick_definitions(
        definitions, "src/use.c", "int f(void) { return ping(); }\n", 1, 1, [1]
    )
    assert [d.name for d in picked] == ["ping", "pong"]


def test_pick_puts_the_implementation_right_after_its_alias():
    definitions = [
        macro("api_read", "#define api_read(x) drv_read(x)"),
        function("drv_read"),
        macro("BUF_LEN", "#define BUF_LEN 16", first=5),
    ]
    text = "int f(int i)\n{\n    return api_read(i) + BUF_LEN;\n}\n"

    picked = pick_definitions(definitions, "src/use.c", text, 1, 4, [3])

    assert [d.name for d in picked] == ["api_read", "drv_read", "BUF_LEN"]


CALLEE = "int divide(int a, int b)\n{\n    return a / b;\n}\n"
CALLER_TEXT = (
    '#include "calc.h"\n'
    "int update(int sum)\n"
    "{\n"
    "    int n = read_count();\n"
    "    /* divide(sum, 0) in a comment */\n"
    "    return divide(sum, n);\n"
    "}\n"
    "int twice(int x)\n"
    "{\n"
    "    return DIV(x, 2) + divide(x, 3);\n"
    "}\n"
)
NAMES = {"divide": "divide", "DIV": "divide"}


def spans(definitions):
    return [(d.name, d.kind, d.file, d.first, d.last) for d in definitions]


def test_function_blocks_and_enclosing_function():
    assert function_blocks(CALLER_TEXT) == [("update", 2, 7), ("twice", 8, 11)]
    assert enclosing_function(CALLEE, 3) == "divide"
    assert enclosing_function(CALLEE, 1) == "divide"  # 서명 줄도 그 함수다
    assert enclosing_function("int g;\nint f(void)\n{\n}\n", 1) is None
    assert enclosing_function("struct s {\n    int a;\n};\n", 2) is None


def test_call_sites_skip_comments_and_merge_per_function():
    sites = find_call_sites("app/main.c", CALLER_TEXT, NAMES)

    assert spans(sites) == [
        ("divide", "caller", "app/main.c", 2, 7),
        ("divide", "caller", "app/main.c", 8, 11),
    ]
    assert sites[0].text.startswith("int update(int sum)\n{")


def test_recursion_is_not_a_call_site():
    text = "int divide(int a, int b)\n{\n    return b ? divide(a, b - 1) : a;\n}\n"

    assert find_call_sites("calc.c", text, {"divide": "divide"}) == []


def test_window_is_cut_to_the_function_and_long_bodies_split():
    body = "\n".join(f"    v = divide(v, {i});" for i in range(100))
    text = f"int run(int v)\n{{\n{body}\n    return v;\n}}\n"

    sites = find_call_sites("run.c", text, {"divide": "divide"})

    assert sites[0].first == 1
    assert sites[-1].last == 104
    assert all(d.last - d.first + 1 <= 80 for d in sites)
    assert all(a.last < b.first for a, b in zip(sites, sites[1:], strict=False))


def test_callers_for_follows_alias_macros_and_keeps_the_home_file_first():
    found = [Definition("DIV", "macro", "app/calc.h", 1, 1, "#define DIV(a, b) divide(a, b)")]
    texts = {
        "app/calc.c": CALLEE + "int half(int v)\n{\n    return divide(v, 2);\n}\n",
        "app/calc.h": "#define DIV(a, b) divide(a, b)\n",
        "app/main.c": CALLER_TEXT,
        "app/zz.c": "int other(int v)\n{\n    return v;\n}\n",
    }

    kept = callers_for(found, texts, {"divide": "app/calc.c"})

    assert [(d.file, d.first) for d in kept] == [
        ("app/calc.c", 5),
        ("app/main.c", 2),
        ("app/main.c", 8),
    ]


def test_callers_for_stores_at_most_the_cap_per_function():
    many = "".join(
        f"int c{i}(int v)\n{{\n    return divide(v, {i});\n}}\n"
        for i in range(MAX_STORED_CALLERS + 5)
    )

    kept = callers_for([], {"many.c": many}, {"divide": "calc.c"})

    assert len(kept) == MAX_STORED_CALLERS


def test_pick_callers_caps_count_and_lines_and_counts_the_rest():
    def caller(file, first, size):
        return Definition("divide", "caller", file, first, first + size - 1, "x")

    callers = [caller("b.c", 1, 10), caller("calc.c", 50, 10), caller("a.c", 1, 10)]
    callers += [caller("c.c", 10 * i, 10) for i in range(1, 5)]
    callers.append(Definition("other", "caller", "a.c", 1, 5, "x"))

    picked, hidden = pick_callers(callers, "divide", "calc.c")

    assert [(d.file, d.first) for d in picked] == [
        ("calc.c", 50),
        ("a.c", 1),
        ("b.c", 1),
        ("c.c", 10),
    ]
    assert len(picked) == MAX_CALLERS and hidden == 3

    big = [caller("a.c", 1, MAX_CALLER_LINES - 5), caller("b.c", 1, 10)]
    picked, hidden = pick_callers(big, "divide", "calc.c")
    assert [d.file for d in picked] == ["a.c"] and hidden == 1

    picked, _ = pick_callers(callers, "divide", "calc.c", available={"a.c"})
    assert [d.file for d in picked] == ["a.c"]


def test_pick_definitions_never_picks_caller_fragments():
    fragment = Definition("divide", "caller", "main.c", 2, 7, "int update(int sum)")
    text = "int divide(int a, int b)\n{\n    return a / b;\n}\n"

    assert pick_definitions([fragment], "calc.c", text, 1, 4, [3]) == []


def test_scan_project_returns_texts(tmp_path):
    (tmp_path / "a.c").write_bytes(b"int f(void)\n{\n    return 0;\n}\n")

    definitions, unreadable, texts = scan_project(tmp_path)

    assert [d.name for d in definitions] == ["f"]
    assert unreadable == 0 and texts == {"a.c": "int f(void)\n{\n    return 0;\n}\n"}
    assert CALLER_CONTEXT == 10


def calls_divide(name: str, number: int) -> str:
    return f"int {name}(int v)\n{{\n    return divide(v, {number});\n}}\n"


def fragments(kept):
    return [(d.file, d.first) for d in kept if d.kind == "caller"]


def test_pick_callers_skips_home_fragments_inside_the_sent_code():
    home = [Definition("divide", "caller", "calc.c", 10 * i, 10 * i + 5, "x") for i in (1, 2, 3, 4)]
    other = Definition("divide", "caller", "main.c", 1, 5, "x")

    picked, hidden = pick_callers([*home, other], "divide", "calc.c", first=1, last=60)
    assert picked == [other] and hidden == 0

    picked, hidden = pick_callers([*home, other], "divide", "calc.c", first=1, last=25)
    assert fragments(picked) == [("calc.c", 30), ("calc.c", 40), ("main.c", 1)]
    assert hidden == 0


def test_callers_for_keeps_other_files_when_the_home_file_is_busy():
    busy = "".join(calls_divide(f"c{i}", i) for i in range(MAX_STORED_CALLERS + 5))
    texts = {"calc.c": CALLEE + busy, "main.c": CALLER_TEXT}

    kept = fragments(callers_for([], texts, {"divide": "calc.c"}))

    assert [file for file, _ in kept].count("calc.c") == MAX_STORED_CALLERS
    assert ("main.c", 2) in kept and ("main.c", 8) in kept


def scale(word: str, factor: int) -> str:
    return f"{word}int scale(int v)\n{{\n    return v * {factor};\n}}\n"


RUN_SCALE = "int run(int v)\n{\n    return scale(v);\n}\n"


def callers_of_scale(texts):
    found = [d for file, text in texts.items() for d in find_definitions(file, text)]
    return fragments(callers_for(found, texts, {"scale": "a.c"}))


def test_static_function_callers_stay_in_their_file():
    texts = {"a.c": scale("static ", 2) + RUN_SCALE, "b.c": scale("static ", 3) + RUN_SCALE}
    assert callers_of_scale(texts) == [("a.c", 5)]

    texts = {"a.c": scale("static ", 2) + RUN_SCALE, "b.c": RUN_SCALE}
    assert callers_of_scale(texts) == [("a.c", 5)]


def test_unique_function_keeps_callers_in_other_files():
    texts = {"a.c": scale("", 2) + RUN_SCALE, "b.c": RUN_SCALE}

    assert callers_of_scale(texts) == [("a.c", 5), ("b.c", 1)]


def test_function_defined_in_two_files_keeps_only_home_callers():
    texts = {"a.c": scale("", 2) + RUN_SCALE, "b.c": scale("", 3), "c.c": RUN_SCALE}

    assert callers_of_scale(texts) == [("a.c", 5)]


def test_call_sites_accept_blanked_code():
    code = split_lines(blank_comments(CALLER_TEXT))

    assert function_blocks(CALLER_TEXT, code) == function_blocks(CALLER_TEXT)
    assert find_call_sites("m.c", CALLER_TEXT, NAMES, code) == find_call_sites(
        "m.c", CALLER_TEXT, NAMES
    )


def test_callers_for_skips_files_without_the_names(monkeypatch):
    scanned = []
    real = csource.find_call_sites

    def record(file, text, names, code=None):
        scanned.append(file)
        return real(file, text, names, code)

    monkeypatch.setattr(csource, "find_call_sites", record)
    texts = {"calc.c": CALLEE, "main.c": CALLER_TEXT, "zz.c": "int other(int v)\n{\n}\n"}

    callers_for([], texts, {"divide": "calc.c"})

    assert scanned == ["calc.c", "main.c"]


def test_callers_for_counts_the_name_used_without_a_call():
    table = (
        "#define DIVIDE_FN divide\n"
        "op_t ops[] = { divide, 0 };\n"
        "int pick(void)\n"
        "{\n"
        "    op_t f = &divide; /* divide */\n"
        "    return f(1, 1) + s.divide + p->divide;\n"
        "}\n"
    )
    texts = {"calc.c": CALLEE, "main.c": CALLER_TEXT, "ops.c": table}

    kept = callers_for([], texts, {"divide": "calc.c"})

    count = Definition("divide", "caller_ref", "", 0, 0, "2")
    assert [d for d in kept if d.kind == "caller_ref"] == [count]
    plain = callers_for([], {"main.c": CALLER_TEXT}, {"divide": "calc.c"})
    assert [d.kind for d in plain] == ["caller", "caller"]


def test_pick_ignores_caller_name_counts():
    count = Definition("divide", "caller_ref", "", 0, 0, "2")

    assert pick_definitions([count], "calc.c", CALLEE, 1, 4, [3]) == []
    assert pick_callers([count], "divide", "calc.c") == ([], 0)


def test_pick_places_function_bodies_before_types_and_plain_macros(monkeypatch):
    import llm_lab.csource as csource

    monkeypatch.setattr(csource, "MAX_DEFINITIONS", 3)
    definitions = [
        Definition("sig_t", "type", "inc/types.h", 1, 1, "typedef int sig_t;"),
        Definition("LIMIT", "macro", "inc/types.h", 2, 2, "#define LIMIT 4"),
        Definition("api_get", "macro", "inc/api.h", 1, 1, "#define api_get(x) impl_get(x)"),
        Definition("impl_get", "function", "gen/impl.c", 1, 3, "int impl_get(int x)\n{\n}"),
        Definition("far_call", "function", "lib/far.c", 1, 3, "int far_call(void)\n{\n}"),
    ]
    text = (
        "int f(sig_t v)\n"
        "{\n"
        "    int a = api_get(v) / LIMIT;\n"
        "    a += far_call();\n"
        "    return a;\n"
        "}\n"
    )

    picked = pick_definitions(definitions, "src/use.c", text, 1, 6, [3])

    # 검사 줄 근처의 함수(별칭 매크로와 그 구현)가 먼저, 그다음 멀리 있는 함수, 타입·매크로는 뒤
    assert [d.name for d in picked] == ["api_get", "impl_get", "far_call"]


def test_definition_caps_leave_room_for_rte_heavy_code():
    assert MAX_DEFINITIONS == 40
    assert MAX_DEFINITION_TOTAL_LINES == 1500
