import io

import pytest
from codemetrics_fixtures import GRADE_C, GRADE_REFACTORED, code_answer, metric_row, metrics_excel
from fake_llm import MODEL, make_client, scripted

from llm_lab.fixer import FixError, FixResult, apply_edits, make_request, propose_fixes
from llm_lab.kinds.codemetrics import (
    BRACES,
    CALLING_FIX,
    CODE_SCHEMA,
    CODEMETRICS,
    COMMENT_DROPPED,
    SIGNATURE_CHANGED,
)
from llm_lab.polyspace import read_sheet
from llm_lab.sources import decode_source

SOURCE = decode_source("src/grade.c", GRADE_C)
FIXED = '#include "grade.h"\n\n' + GRADE_REFACTORED  # 범위(3~15줄)를 code로 바꾼 파일
GROUP = (
    metric_row(actual=24),
    metric_row("Number of Calling Functions", threshold=4, actual=6),
)


def request_for(*rows: list):
    sheet = read_sheet(io.BytesIO(metrics_excel(*rows)), CODEMETRICS, CODEMETRICS.defaults)
    return make_request(CODEMETRICS, sheet.findings, SOURCE, sheet.prepared.data)


def ask(request, *responses):
    handler, sent = scripted(*responses)
    return propose_fixes(make_client(handler), MODEL, request), sent


def test_fix_rows_share_the_refactoring_and_calling_rows_keep_only_a_reason():
    results, sent = ask(
        request_for(*GROUP),
        code_answer(
            ("fix", "등급 계산을 도우미로 옮김"), ("no_fix", "공용 함수"), code=GRADE_REFACTORED
        ),
    )

    assert sent[0]["response_format"]["json_schema"]["schema"] == CODE_SCHEMA
    fix, calling = results
    assert (fix.decision, fix.reason) == ("fix", "등급 계산을 도우미로 옮김")
    assert apply_edits(SOURCE.text, fix.edits) == FIXED
    assert calling == FixResult("no_fix", "공용 함수", (), "")
    # 바뀐 줄만 남는다: 원래 주석과 시그니처는 diff에서 빠지지 않는다
    assert "+static int grade_band(int score)" in fix.diff
    assert "-/* 점수를 등급으로 바꾼다. */" not in fix.diff
    assert "-int grade(int score)" not in fix.diff


def test_when_nothing_is_fixed_the_code_is_ignored():
    results, _ = ask(request_for(*GROUP), code_answer(("no_fix", "a"), ("no_fix", "b"), code="{"))

    assert results == [FixResult("no_fix", "a", (), ""), FixResult("no_fix", "b", (), "")]


def test_fixing_the_calling_functions_row_is_refused_and_asked_again():
    request = request_for(*GROUP)
    wrong = code_answer(("fix", "x"), ("fix", "y"), code=GRADE_REFACTORED)
    right = code_answer(("fix", "x"), ("no_fix", "y"), code=GRADE_REFACTORED)

    results, sent = ask(request, wrong, right)

    assert [result.decision for result in results] == ["fix", "no_fix"]
    assert (
        "Finding 2은 Number of Calling Functions라 no_fix여야 함"
        in sent[1]["messages"][-1]["content"]
    )


@pytest.mark.parametrize(
    ("code", "summary"),
    [
        (
            GRADE_REFACTORED.replace("int grade(int score)", "int grade(long score)"),
            SIGNATURE_CHANGED,
        ),
        (
            GRADE_REFACTORED.replace("\nint grade(int score)", "\nstatic int grade(int score)"),
            SIGNATURE_CHANGED,
        ),
        (GRADE_REFACTORED.replace("/* 점수를 등급으로 바꾼다. */\n", ""), COMMENT_DROPPED),
        (GRADE_REFACTORED + "}\n", BRACES),
    ],
)
def test_broken_code_is_asked_again_and_then_explained(code, summary):
    bad = code_answer(("fix", "x"), code=code)

    with pytest.raises(FixError) as caught:
        ask(request_for(metric_row()), bad, bad)

    assert str(caught.value).splitlines()[0] == summary


def test_calling_fix_twice_is_explained():
    wrong = code_answer(("fix", "x"), ("fix", "y"), code=GRADE_REFACTORED)

    with pytest.raises(FixError) as caught:
        ask(request_for(*GROUP), wrong, wrong)

    assert str(caught.value).splitlines()[0] == CALLING_FIX


def test_line_number_prefixes_copied_from_the_shown_code_are_removed():
    numbered = "\n".join(
        f"{number:>5}| {line}" for number, line in enumerate(GRADE_REFACTORED.splitlines(), 3)
    )

    results, _ = ask(request_for(metric_row()), code_answer(("fix", "x"), code=numbered))

    assert apply_edits(SOURCE.text, results[0].edits) == FIXED


def test_characters_the_file_encoding_cannot_hold_are_asked_again():
    emoji = GRADE_REFACTORED.replace("등급.", "등급 \U0001f600.")
    request = request_for(metric_row())

    results, sent = ask(
        request,
        code_answer(("fix", "x"), code=emoji),
        code_answer(("fix", "x"), code=GRADE_REFACTORED),
    )

    assert "U+1F600" in sent[1]["messages"][-1]["content"]
    assert apply_edits(SOURCE.text, results[0].edits) == FIXED


def test_missing_numbers_unchanged_code_and_cut_answers_are_asked_again():
    request = request_for(*GROUP)
    original = SOURCE.text.split("\n", 2)[2]  # 3줄부터 끝까지 = 바꿀 범위 그대로

    _, sent = ask(
        request,
        code_answer(("fix", "x"), code=GRADE_REFACTORED),  # 2번이 빠짐
        code_answer(("fix", "x"), ("no_fix", "y"), code=GRADE_REFACTORED),
    )
    assert "findings의 number는 1부터 2까지" in sent[1]["messages"][-1]["content"]

    with pytest.raises(FixError) as caught:
        same = code_answer(("fix", "x"), ("no_fix", "y"), code=original)
        ask(request, same, same)
    assert "수정해도 원본과 같음" in str(caught.value)

    with pytest.raises(FixError) as caught:
        cut = code_answer(("fix", "x"), ("no_fix", "y"), code="", finish_reason="length")
        ask(request, cut, cut)
    assert str(caught.value).startswith("LLM 답이 길이 제한에 걸려 잘렸습니다.")


def test_kind_waits_longer_than_rte():
    assert CODEMETRICS.timeout == 300.0


def test_prefixes_copied_on_some_lines_only_are_removed_too():
    # 원래 줄은 보인 그대로(번호 포함) 베끼고, 새 도우미 줄은 번호 없이 쓰는 답
    lines = GRADE_REFACTORED.splitlines()
    helper_end = lines.index("") + 1  # 도우미와 빈 줄까지는 번호 없음
    original = [f"{number:>5}| {line}" for number, line in enumerate(lines[helper_end:], 3)]
    mixed = "\n".join([*lines[:helper_end], *original])

    results, _ = ask(request_for(metric_row()), code_answer(("fix", "x"), code=mixed))

    assert apply_edits(SOURCE.text, results[0].edits) == FIXED
