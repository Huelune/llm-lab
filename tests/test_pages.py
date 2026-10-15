from llm_lab import pages
from llm_lab.pages import _diff, index_page


def test_diff_colours_content_lines_that_look_like_headers():
    diff = "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n---x;\n+++x;"

    html = _diff(diff)

    assert '<span class="hunk">--- a/x.c</span>' in html
    assert '<span class="del">---x;</span>' in html
    assert '<span class="add">+++x;</span>' in html


def test_app_name_and_tagline_on_the_home_page():
    html = index_page([], [])

    assert pages.APP_NAME == "LLM Test Advisor"
    assert "<title>새 분석 · LLM Test Advisor</title>" in html
    assert '<h1 class="app-title">LLM Test Advisor</h1>' in html
    assert pages.APP_TAGLINE in html
    assert "Polyspace 수정 제안" not in html
