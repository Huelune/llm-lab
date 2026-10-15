import json

from llm_lab import pages
from llm_lab.pages import _diff, index_page
from llm_lab.store import Job


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


def _job(**changes):
    settings = {
        "connection": "게이트웨이 B",
        "model": "secret-model",
        "kind": "rte",
        "mode": "guard",
        "choices": ["Orange", "Red"],
        "follow_ups": 2,
        "source_root": "D:/secret",
        "excel": "secret.xlsx",
    }
    fields = dict(
        id="0123456789abcdef",
        created_at="2026-10-15T09:30:00",
        excel_name="secret.xlsx",
        sheet="RTE_Result",
        skipped=0,
        state="done",
        message="",
        connection="게이트웨이 B",
        title="secret 작업",
        settings=json.dumps(settings, ensure_ascii=False),
    )
    return Job(**{**fields, **changes})


def test_job_page_shows_settings_card_and_rename_form():
    html = pages.job_page(_job(), [], None)

    for text in (
        "작업 설정",
        "게이트웨이 B",
        "secret-model",
        "가드 허용",
        "Orange, Red",
        "이름 바꾸기",
    ):
        assert text in html
    assert '<form method="post" action="/results/0123456789abcdef/rename">' in html
    assert "<title>secret 작업 · LLM Test Advisor</title>" in html


def test_job_page_without_recorded_settings_says_so():
    html = pages.job_page(_job(settings="", title=""), [], None)

    assert "기록된 설정이 없습니다" in html
    assert "<h1>secret.xlsx " in html


def test_job_lists_show_name_connection_and_model():
    html = pages.results_page([(_job(), [])])

    for text in ("secret 작업", "게이트웨이 B", "secret-model", "<th>모델</th>"):
        assert text in html


def test_summary_page_has_neutral_title_and_no_names():
    html = pages.summary_page(_job(), [])

    assert "<title>작업 요약 · RTE · 01234567 · LLM Test Advisor</title>" in html
    assert "secret" not in html
    assert "게이트웨이" not in html


def test_auto_refresh_waits_while_a_form_is_being_typed_in():
    # 처리 중 자동 새로 고침이 이름 바꾸기 칸에 적던 글을 지우지 않는다
    assert "document.activeElement.closest('form')" in pages.SCRIPT
    assert "document.querySelector('details.rename[open]')" in pages.SCRIPT
