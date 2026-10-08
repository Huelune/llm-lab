import pytest
from fake_llm import MODEL, completion, error, make_client, scripted
from rte_fixtures import CALC_C, REASON, finding, fix_answer

from llm_lab import fixer
from llm_lab.fixer import FatalLLMError, FixError, apply_edits, cache_key, propose_fix
from llm_lab.sources import decode_source

SOURCE = decode_source("calc.c", CALC_C)


def test_fix_returns_edits_diff_and_sends_schema_and_numbered_code():
    handler, sent = scripted(fix_answer())

    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)

    assert result.decision == "fix"
    assert result.reason == REASON
    assert "(b != 0) ? a / b : 0;" in apply_edits(SOURCE.text, result.edits)
    assert "+    return (b != 0) ? a / b : 0;" in result.diff
    request = sent[0]
    assert request["model"] == MODEL
    assert request["response_format"]["json_schema"]["name"] == "rte_fix"
    assert request["messages"][0]["role"] == "system"
    prompt = request["messages"][1]["content"]
    assert "- check: Division by zero" in prompt
    assert "- line: 3" in prompt
    assert "    3|     return a / b;" in prompt
    assert "max_tokens" not in request  # 생각 토큰 때문에 출력 한도를 걸지 않는다


def test_no_fix_ignores_edits():
    handler, _ = scripted(fix_answer("no_fix", edits=[{"original": "x", "replacement": "y"}]))

    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)

    assert (result.decision, result.edits, result.diff) == ("no_fix", (), "")


def test_unusable_answer_is_retried_once_with_the_problem():
    wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
    handler, sent = scripted(wrong, fix_answer())

    result = propose_fix(make_client(handler), MODEL, finding(), SOURCE)

    assert result.decision == "fix"
    retry = sent[1]["messages"]
    assert retry[2]["role"] == "assistant"
    assert "edits[1].original을 보낸 코드에서 찾을 수 없음" in retry[3]["content"]


def test_replacement_not_writable_in_source_encoding_is_retried():
    # CP949 소스에 CP949로 쓸 수 없는 문자(– U+2013)를 넣으면 패치를 만들 수 없으므로 다시 요청한다
    source = decode_source("calc.c", ("/* 나눗셈 */\n" + CALC_C.decode()).encode("cp949"))
    dash = {"original": "    return a / b;", "replacement": "    return b ? a / b : 0; /* b – 0 */"}
    handler, sent = scripted(fix_answer(edits=[dash]), fix_answer())

    result = propose_fix(make_client(handler), MODEL, finding(line=4), source)

    assert result.decision == "fix"
    assert "–" not in apply_edits(source.text, result.edits)
    assert "cp949로 쓸 수 없는 문자 U+2013" in sent[1]["messages"][3]["content"]


def test_transcript_records_each_request_and_response():
    wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
    handler, _ = scripted(wrong, fix_answer())
    transcript: list = []

    propose_fix(make_client(handler), MODEL, finding(), SOURCE, transcript)

    first, second = transcript
    assert first.messages[0]["role"] == "system"
    assert "    3|     return a / b;" in first.messages[1]["content"]
    assert '"return a % b;"' in first.content
    assert first.finish_reason == "stop"
    assert (first.prompt_tokens, first.completion_tokens) == (12, 3)
    assert first.raw["choices"][0]["message"]["content"] == first.content
    assert len(second.messages) == 4  # 다시 요청: 이전 답과 문제 설명이 붙는다


def test_transcript_records_api_errors():
    handler, _ = scripted(error(401, "invalid key"))
    transcript: list = []

    with pytest.raises(FatalLLMError):
        propose_fix(make_client(handler), MODEL, finding(), SOURCE, transcript)

    assert "[인증]" in transcript[0].error
    assert transcript[0].raw is None


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (completion("not json"), "JSON이 아님"),
        (fix_answer(finish_reason="length"), "잘림"),
        (fix_answer(edits=[]), "edits가 비었거나"),
        (completion('{"decision": "maybe", "reason": "x", "edits": []}'), "스키마와 다름"),
    ],
)
def test_gives_up_after_two_unusable_answers(bad, message):
    handler, sent = scripted(bad, bad)

    with pytest.raises(FixError, match=message):
        propose_fix(make_client(handler), MODEL, finding(), SOURCE)
    assert len(sent) == 2


def test_auth_error_is_fatal_and_not_retried():
    handler, sent = scripted(error(401, "invalid key"))

    with pytest.raises(FatalLLMError, match="인증"):
        propose_fix(make_client(handler), MODEL, finding(), SOURCE)
    assert len(sent) == 1


def test_other_api_error_fails_only_this_row():
    handler, _ = scripted(error(400, "response_format not supported"))

    with pytest.raises(FixError, match="기능 미지원"):
        propose_fix(make_client(handler), MODEL, finding(), SOURCE)


@pytest.mark.parametrize(("line", "message"), [(None, "정수가 아님"), (9, "9번 줄이 없음")])
def test_bad_line_fails_without_calling_llm(line, message):
    handler, sent = scripted()

    with pytest.raises(FixError, match=message):
        propose_fix(make_client(handler), MODEL, finding(line=line), SOURCE)
    assert sent == []


def test_cache_key_depends_on_file_content_finding_and_prompt_version(monkeypatch):
    key = cache_key(MODEL, SOURCE, finding())

    assert key == cache_key(MODEL, SOURCE, finding(row=99))  # 엑셀 행 번호는 상관없다
    assert key != cache_key(MODEL, decode_source("calc.c", b"int x;\n"), finding())
    assert key != cache_key(MODEL, SOURCE, finding(line=2))
    assert key != cache_key("other-model", SOURCE, finding())
    monkeypatch.setattr(fixer, "PROMPT_VERSION", fixer.PROMPT_VERSION + 1)
    assert key != cache_key(MODEL, SOURCE, finding())
