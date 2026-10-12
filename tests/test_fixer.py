import json
from dataclasses import replace

import pytest
from fake_llm import MODEL, completion, error, make_client, scripted
from misra_fixtures import misra_finding
from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer

from llm_lab.fixer import (
    EditMismatch,
    FatalLLMError,
    FixError,
    FixResult,
    apply_edits,
    cache_key,
    make_request,
    propose_fixes,
)
from llm_lab.kinds import MISRA, RTE
from llm_lab.sources import decode_source

SOURCE = decode_source("calc.c", CALC_C)


def ask(handler, findings=None, *, source=SOURCE, kind=RTE, transcript=None):
    request = make_request(kind, findings or [finding()], source)
    return propose_fixes(make_client(handler), MODEL, request, transcript)


def test_fix_returns_edits_diff_and_sends_schema_and_numbered_code():
    handler, sent = scripted(fix_answer())

    (result,) = ask(handler)

    assert result.decision == "fix"
    assert result.reason == REASON
    assert "(b != 0) ? a / b : 0;" in apply_edits(SOURCE.text, result.edits)
    assert "+    return (b != 0) ? a / b : 0;" in result.diff
    request = sent[0]
    assert request["model"] == MODEL
    schema = request["response_format"]["json_schema"]
    assert schema["name"] == "polyspace_fix"
    assert schema["schema"]["required"] == ["findings", "edits"]
    assert request["messages"][0] == {"role": "system", "content": RTE.system_prompt}
    prompt = request["messages"][1]["content"]
    assert prompt.startswith("Polyspace Code Prover result:\n\nFinding 1:\n- TYPE: Orange Check\n")
    assert "- check: Division by zero" in prompt
    assert "- File: calc.c\n- line: 3\n\nCode (lines 1-4 of calc.c;" in prompt
    assert "    3|     return a / b;" in prompt
    assert "max_tokens" not in request  # 생각 토큰 때문에 출력 한도를 걸지 않는다


def test_no_fix_ignores_edits():
    handler, _ = scripted(fix_answer("no_fix", edits=[{"original": "x", "replacement": "y"}]))

    (result,) = ask(handler)

    assert (result.decision, result.edits, result.diff) == ("no_fix", (), "")


def test_group_gets_a_decision_per_finding_and_shares_the_edits():
    findings = [finding(), finding(row=3, check="Overflow")]
    handler, sent = scripted(
        answer(("no_fix", "범위가 보장됨"), ("fix", REASON), edits=[DIVIDE_FIX])
    )

    first, second = ask(handler, findings)

    assert (first.decision, first.reason, first.edits, first.diff) == (
        "no_fix",
        "범위가 보장됨",
        (),
        "",
    )
    assert (second.decision, second.reason) == ("fix", REASON)
    assert "(b != 0) ? a / b : 0;" in apply_edits(SOURCE.text, second.edits)
    prompt = sent[0]["messages"][1]["content"]
    assert "Finding 1:\n- TYPE: Orange Check\n- check: Division by zero\n" in prompt
    assert "Finding 2:\n- TYPE: Orange Check\n- check: Overflow\n" in prompt
    assert len(sent) == 1


@pytest.mark.parametrize("numbers", [[1], [1, 1], [1, 3], [2, 1, 3]])
def test_findings_must_answer_each_number_once(numbers):
    body = {
        "findings": [{"number": n, "decision": "no_fix", "reason": "r"} for n in numbers],
        "edits": [],
    }
    handler, sent = scripted(completion(json.dumps(body)), completion(json.dumps(body)))

    with pytest.raises(FixError) as caught:
        ask(handler, [finding(), finding(row=3)])

    first, _, more = str(caught.value).split("\n")
    assert first == "LLM 답에 빠진 행이 있습니다."
    assert "1부터 2까지 한 번씩" in more
    assert "findings의 number" in sent[1]["messages"][3]["content"]


def test_unusable_answer_is_retried_once_with_the_problem():
    wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
    handler, sent = scripted(wrong, fix_answer())

    (result,) = ask(handler)

    assert result.decision == "fix"
    retry = sent[1]["messages"]
    assert retry[2]["role"] == "assistant"
    assert "edits[1].original을 보낸 코드에서 찾을 수 없음" in retry[3]["content"]


def test_replacement_not_writable_in_source_encoding_is_retried():
    # CP949 소스에 CP949로 쓸 수 없는 문자(– U+2013)를 넣으면 패치를 만들 수 없으므로 다시 요청한다
    source = decode_source("calc.c", ("/* 나눗셈 */\n" + CALC_C.decode()).encode("cp949"))
    dash = {"original": "    return a / b;", "replacement": "    return b ? a / b : 0; /* b – 0 */"}
    handler, sent = scripted(fix_answer(edits=[dash]), fix_answer())

    (result,) = ask(handler, [finding(line=4)], source=source)

    assert result.decision == "fix"
    assert "–" not in apply_edits(source.text, result.edits)
    assert "cp949로 쓸 수 없는 문자 U+2013" in sent[1]["messages"][3]["content"]


def test_transcript_records_each_request_and_response():
    wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
    handler, _ = scripted(wrong, fix_answer())
    transcript: list = []

    ask(handler, transcript=transcript)

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
        ask(handler, transcript=transcript)

    assert "[인증]" in transcript[0].error
    assert transcript[0].raw is None


@pytest.mark.parametrize(
    ("bad", "summary", "detail"),
    [
        (completion("not json"), "LLM 답을 읽을 수 없었습니다.", "JSON이 아님"),
        (completion("[]"), "LLM 답을 읽을 수 없었습니다.", "JSON 객체가 아님"),
        (completion('{"findings": "x"}'), "LLM 답을 읽을 수 없었습니다.", "스키마와 다름"),
        (fix_answer(finish_reason="length"), "LLM 답이 길이 제한에 걸려 잘렸습니다.", "잘림"),
        (fix_answer(edits=[]), "LLM 답을 읽을 수 없었습니다.", "edits가 비었거나"),
        (
            fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}]),
            "LLM이 고칠 위치를 정확히 짚지 못했습니다.",
            "찾을 수 없음",
        ),
    ],
)
def test_gives_up_after_two_unusable_answers_with_a_plain_message(bad, summary, detail):
    handler, sent = scripted(bad, bad)

    with pytest.raises(FixError) as caught:
        ask(handler)

    # 화면용: 첫 줄은 무엇이 문제인지, 다음 줄은 할 일, 개발자용 내용은 "자세히:" 뒤에
    first, todo, more = str(caught.value).split("\n")
    assert first == summary
    assert todo.startswith("할 일: ") and "다시 시도" in todo
    assert more.startswith("자세히: ") and detail in more
    assert len(sent) == 2


def test_repeated_code_is_resolved_with_the_reported_line():
    # LLM이 고칠 줄로 보낸 코드가 파일에 두 군데 있어도 Polyspace가 가리킨 줄 쪽을 고친다
    source = decode_source("calc.c", b"int f(int a)\n{\n    a = a + 1;\n    a = a + 1;\n}\n")
    edit = {"original": "    a = a + 1;", "replacement": "    a = inc(a);"}
    handler, sent = scripted(fix_answer(edits=[edit]))

    (result,) = ask(handler, [finding(line=4)], source=source)

    assert apply_edits(source.text, result.edits).endswith("    a = a + 1;\n    a = inc(a);\n}\n")
    assert len(sent) == 1


def test_rte_prompt_asks_for_small_edits_and_the_common_answer():
    assert "only the lines you change" in RTE.system_prompt
    assert '"findings" has one entry for each numbered finding' in RTE.system_prompt


def test_misra_request_sends_the_category_and_the_same_line_rules():
    findings = [misra_finding(category="Mandatory"), misra_finding(row=4, check="12.1 Precedence")]

    request = make_request(MISRA, findings, SOURCE)

    prompt = request.messages[1]["content"]
    assert prompt.startswith(
        "Polyspace MISRA C:2012 results:\n\nFinding 1:\n- TYPE: MISRA C:2012\n"
    )
    assert "- category: Mandatory\n" in prompt
    assert "Finding 2:\n- TYPE: MISRA C:2012\n- check: 12.1 Precedence\n" in prompt
    assert "information" not in prompt
    system = request.messages[0]["content"]
    assert "All findings in one request are on the same line." in system
    assert "Mandatory: no deviation is allowed" in system
    assert "do not cast a composite expression (Rule 10.8)" in system


def test_auth_error_is_fatal_and_not_retried():
    handler, sent = scripted(error(401, "invalid key"))

    with pytest.raises(FatalLLMError, match="인증"):
        ask(handler)
    assert len(sent) == 1


def test_other_api_error_fails_only_this_group():
    handler, _ = scripted(error(400, "response_format not supported"))

    with pytest.raises(FixError) as caught:
        ask(handler)

    message = str(caught.value)
    assert message.startswith("LLM 서버가 요청을 처리하지 못했습니다.\n할 일: ")
    assert "기능 미지원" in message


@pytest.mark.parametrize(
    ("line", "message"), [(None, "line 칸이 정수가 아님"), (9, "9번 줄이 없음")]
)
def test_bad_line_is_found_before_calling_llm(line, message):
    with pytest.raises(FixError) as caught:
        make_request(RTE, [finding(), finding(row=4, line=line)], SOURCE)

    assert str(caught.value).startswith("엑셀의 줄 번호가 소스와 맞지 않습니다.\n할 일: ")
    assert f"엑셀 4행: {message}" in str(caught.value)


def test_cache_key_depends_on_the_messages_file_and_kind():
    def key(findings=None, *, source=SOURCE, kind=RTE, model=MODEL):
        return cache_key(model, make_request(kind, findings or [finding()], source))

    assert key() == key([finding(row=99)])  # 엑셀 행 번호는 상관없다
    assert key() != key(source=decode_source("calc.c", CALC_C + b"int x;\n"))
    assert key() != key([finding(line=2)])
    assert key() != key([finding(), finding(row=3)])
    assert key() != key(model="other-model")
    assert key() != key(kind=replace(RTE, prompt_version=RTE.prompt_version + 1))
    assert key() != key(kind=replace(RTE, system_prompt=RTE.system_prompt + "Be brief.\n"))


def test_kind_can_add_a_message_tail_its_own_answer_format_and_timeout():
    schema = {
        "type": "object",
        "properties": {"verdict": {"type": "string"}},
        "required": ["verdict"],
        "additionalProperties": False,
    }

    def tail(context, findings, source):
        return f"Limit {context['limit']} for {len(findings)} finding(s) in {source.name}."

    def to_results(data, request):
        assert request.context == {"limit": 10}
        return [FixResult("no_fix", data["verdict"], (), "")]

    kind = replace(
        RTE, prompt_tail=tail, answer_schema=schema, to_results=to_results, timeout=300.0
    )
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions["timeout"]["read"])
        return completion('{"verdict": "그대로 둠"}')

    request = make_request(kind, [finding()], SOURCE, {"limit": 10})
    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.reason == "그대로 둠"
    prompt = request.messages[1]["content"]
    assert "- line: 3\n\nLimit 10 for 1 finding(s) in calc.c.\n\nCode (lines" in prompt
    assert timeouts == [300.0]


def test_kind_answer_check_can_name_its_own_problem():
    def to_results(data, request):
        raise EditMismatch("함수 시그니처가 바뀜", summary="LLM이 함수 이름이나 인자를 바꿨습니다.")

    handler, sent = scripted(fix_answer(), fix_answer())

    with pytest.raises(FixError) as caught:
        ask(handler, kind=replace(RTE, to_results=to_results))

    first, _, more = str(caught.value).split("\n")
    assert first == "LLM이 함수 이름이나 인자를 바꿨습니다."
    assert more == "자세히: 함수 시그니처가 바뀜 (2번 시도)"
    assert "함수 시그니처가 바뀜" in sent[1]["messages"][3]["content"]
