from llm_lab.pages import _diff


def test_diff_colours_content_lines_that_look_like_headers():
    diff = "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n---x;\n+++x;"

    html = _diff(diff)

    assert '<span class="hunk">--- a/x.c</span>' in html
    assert '<span class="del">---x;</span>' in html
    assert '<span class="add">+++x;</span>' in html
