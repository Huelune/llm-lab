from llm_lab import pages
from llm_lab.pages import _diff, index_page


def test_diff_colours_content_lines_that_look_like_headers():
    diff = "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n---x;\n+++x;"

    html = _diff(diff)

    assert '<span class="hunk" data-ln="          ">--- a/x.c</span>' in html
    assert '<span class="del" data-ln="    1     ">---x;</span>' in html
    assert '<span class="add" data-ln="         1">+++x;</span>' in html


def test_diff_lines_carry_old_and_new_line_numbers_across_files():
    # 줄 번호는 @@ 머리의 시작 줄에서 센다. 다른 파일까지 고치는 diff는 파일 머리가 또 나온다
    diff = (
        "--- a/a.c\n+++ b/a.c\n@@ -10,3 +10,3 @@\n x;\n-y;\n+z;\n w;\n"
        "--- a/b.c\n+++ b/b.c\n@@ -5 +5,2 @@\n-p;\n+q;\n+r;"
    )

    html = _diff(diff)

    assert '<span data-ln="   10   10"> x;</span>' in html
    assert '<span class="del" data-ln="   11     ">-y;</span>' in html
    assert '<span class="add" data-ln="        11">+z;</span>' in html
    assert '<span data-ln="   12   12"> w;</span>' in html
    assert '<span class="hunk" data-ln="          ">--- a/b.c</span>' in html
    assert '<span class="add" data-ln="         6">+r;</span>' in html


def test_app_name_and_tagline_on_the_home_page():
    html = index_page([], [])

    assert pages.APP_NAME == "LLM Test Advisor"
    assert "<title>새 분석 · LLM Test Advisor</title>" in html
    assert '<h1 class="app-title">LLM Test Advisor</h1>' in html
    assert pages.APP_TAGLINE in html
    assert "Polyspace 수정 제안" not in html
