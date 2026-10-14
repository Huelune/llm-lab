import json
from dataclasses import replace

import pytest
from fake_llm import MODEL, completion, error, make_client, scripted
from misra_fixtures import misra_finding
from rte_fixtures import CALC_C, DIVIDE_FIX, REASON, answer, finding, fix_answer

from llm_lab.csource import MAX_DEFINITION_LINES, MAX_STORED_CALLERS, Definition
from llm_lab.fixer import (
    FIX_SCHEMA_WITH_FILE,
    MISMATCH_SUMMARIES,
    EditMismatch,
    FatalLLMError,
    FixError,
    FixResult,
    apply_edits,
    cache_key,
    make_request,
    propose_fixes,
)
from llm_lab.kinds import CODEMETRICS, MISRA, RTE
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
    assert prompt.startswith(
        "Polyspace Code Prover results in one C function:\n\nFinding 1:\n- TYPE: Orange Check\n"
    )
    assert "- check: Division by zero" in prompt
    assert "- File: calc.c\n- line: 3\n\nCode (lines 1-4 of calc.c;" in prompt
    assert "    3|     return a / b;" in prompt
    assert "max_tokens" not in request  # 생각 토큰 때문에 출력 한도를 걸지 않는다


def test_no_fix_ignores_edits():
    handler, _ = scripted(fix_answer("no_fix", edits=[{"original": "x", "replacement": "y"}]))

    (result,) = ask(handler)

    assert (result.decision, result.edits, result.diff) == ("no_fix", (), "")


MANUAL_REASON = "호출자가 넘기는 값의 범위를 정해야 합니다."


def test_manual_decision_is_accepted_and_has_no_edits():
    handler, sent = scripted(answer(("manual", MANUAL_REASON)))

    (result,) = ask(handler)

    assert result == FixResult("manual", MANUAL_REASON, (), "")
    findings = sent[0]["response_format"]["json_schema"]["schema"]["properties"]["findings"]
    assert findings["items"]["properties"]["decision"]["enum"] == ["fix", "no_fix", "manual"]


def test_manual_row_in_a_mixed_group_does_not_take_the_shared_edits():
    reply = answer(("fix", REASON), ("manual", MANUAL_REASON), edits=[DIVIDE_FIX])
    handler, _ = scripted(reply)

    first, second = ask(handler, [finding(), finding(row=3, line=1, check="Overflow")])

    assert first.decision == "fix" and first.edits
    assert (second.decision, second.edits, second.diff) == ("manual", (), "")


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


def test_line_past_the_file_end_points_at_the_source_version():
    with pytest.raises(FixError) as caught:
        make_request(RTE, [finding(), finding(row=4, line=9)], SOURCE)

    assert str(caught.value).startswith("엑셀의 줄 번호가 소스와 맞지 않습니다.\n할 일: ")
    assert "엑셀 4행: 9번 줄이 없음" in str(caught.value)


def test_unreadable_line_cell_points_at_the_excel_not_the_source():
    with pytest.raises(FixError) as caught:
        make_request(RTE, [finding(), finding(row=4, line=None)], SOURCE)

    message = str(caught.value)
    assert message.startswith("엑셀 line 칸에서 줄 번호를 읽지 못했습니다.\n할 일: 엑셀에서 ")
    assert "소스 폴더" not in message  # 소스를 바꿔도 해결되지 않는다
    assert "엑셀 4행: line 칸이 비었거나 숫자가 아님" in message


def test_cache_key_depends_on_the_messages_file_and_kind():
    def key(findings=None, *, source=SOURCE, kind=RTE, model=MODEL):
        return cache_key(model, make_request(kind, findings or [finding()], source))

    assert key() == key([finding(row=99)])  # 엑셀 행 번호는 상관없다
    assert key() != key(source=decode_source("calc.c", CALC_C + b"int x;\n"))
    assert key() != key([finding(line=2)])
    assert key() != key([finding(), finding(row=3)])
    assert key() != key(model="other-model")
    assert key() != key(kind=replace(RTE, prompt_version=RTE.prompt_version + 1))
    # 지시문을 작업 정보로 고르는 종류는 system_prompt_for를 비워야 system_prompt가 쓰인다
    reworded = replace(RTE, system_prompt=RTE.system_prompt + "Be brief.\n", system_prompt_for=None)
    assert key() != key(kind=reworded)


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


def test_kind_answer_with_the_wrong_result_count_is_asked_again_then_fails():
    def to_results(data, request):
        return [FixResult("no_fix", "그대로 둠", (), "")]  # 행은 둘인데 결과가 하나

    def valid():  # 행 둘에 대한 올바른 답
        return answer(("fix", REASON), ("fix", REASON), edits=[DIVIDE_FIX])

    handler, sent = scripted(valid(), valid())

    with pytest.raises(FixError) as caught:
        ask(handler, [finding(), finding(row=3)], kind=replace(RTE, to_results=to_results))

    first, _, more = str(caught.value).split("\n")
    assert first == MISMATCH_SUMMARIES["format"]
    assert more == "자세히: 판단 수가 행 수와 다름 (2번 시도)"
    assert len(sent) == 2
    assert "판단 수가 행 수와 다름" in sent[1]["messages"][3]["content"]


LIMIT = Definition("LIMIT", "macro", "inc/limits.h", 3, 3, "#define LIMIT 16")
USER = decode_source("use.c", b"int use(int b)\n{\n    return LIMIT / b;\n}\n")


def test_request_appends_the_definitions_the_code_uses():
    request = make_request(RTE, [finding(line=3)], USER, definitions=[LIMIT])

    assert request.definitions == (LIMIT,)
    content = request.messages[1]["content"]
    assert content.index("```c\n    1| int use(int b)") < content.index(
        "Definitions from other places"
    )
    assert "for reference only. Edits must stay in the numbered code above." in content
    assert "```c\n// inc/limits.h line 3\n#define LIMIT 16\n```" in content


def test_request_without_matching_definitions_has_no_definitions_section():
    unused = Definition("OTHER", "macro", "inc/limits.h", 4, 4, "#define OTHER 1")

    request = make_request(RTE, [finding(line=3)], USER, definitions=[unused])

    assert request.definitions == ()
    assert "Definitions from other places" not in request.messages[1]["content"]


def test_definitions_change_the_cache_key():
    plain = make_request(RTE, [finding(line=3)], USER)
    with_limit = make_request(RTE, [finding(line=3)], USER, definitions=[LIMIT])

    assert cache_key(MODEL, plain) != cache_key(MODEL, with_limit)


def test_codemetrics_requests_ignore_definitions():
    assert CODEMETRICS.uses_definitions is False
    assert RTE.uses_definitions is True and MISRA.uses_definitions is True


def test_rte_mode_in_the_job_context_picks_the_system_prompt():
    guard = make_request(RTE, [finding()], SOURCE, {"mode": "guard"})
    root = make_request(RTE, [finding()], SOURCE, {"mode": "root_cause"})

    assert "a guard is acceptable" in guard.messages[0]["content"]
    assert "Never make such a change" in root.messages[0]["content"]
    assert cache_key(MODEL, guard) != cache_key(MODEL, root)


USE_DRV = decode_source(
    "src/calc.c", b"int divide(int a, int b)\n{\n    return a / drv_scale(b);\n}\n"
)
DRV = decode_source("lib/drv.c", b"int drv_scale(int v)\n{\n    return v - 1;\n}\n")
DRV_DEF = Definition("drv_scale", "function", "lib/drv.c", 1, 4, DRV.text.rstrip("\n"))
DRV_FIX = {"file": "lib/drv.c", "original": "    return v - 1;", "replacement": "    return v + 1;"}


def ask_with(handler, *, definitions=(DRV_DEF,), files=None, kind=RTE, source=USE_DRV):
    files = {"lib/drv.c": DRV} if files is None else files
    request = make_request(kind, [finding(line=3)], source, None, definitions, files)
    return request, propose_fixes(make_client(handler), MODEL, request)


def test_rte_sends_definitions_with_stored_files_as_editable_numbered_code():
    request = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV})

    content = request.messages[1]["content"]
    assert "Those marked editable may be changed" in content
    assert "// editable: lib/drv.c lines 1-4\n    1| int drv_scale(int v)" in content
    assert set(request.files) == {"lib/drv.c"}
    assert RTE.answer_schema is FIX_SCHEMA_WITH_FILE
    assert FIX_SCHEMA_WITH_FILE["properties"]["edits"]["items"]["required"] == [
        "file",
        "original",
        "replacement",
    ]


def test_definitions_without_a_stored_file_stay_reference_only():
    # 예전 작업: 정의 파일 원문이 작업에 없다
    request = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {})
    unset = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], None)

    content = request.messages[1]["content"]
    assert "for reference only" in content
    assert "```c\n// lib/drv.c lines 1-4\nint drv_scale(int v)" in content
    assert "editable" not in content and "reference only:" not in content
    assert request.files == {}
    # 예전 작업은 files가 없어도 {}여도 같은 메시지와 같은 캐시 키다
    assert request.messages == unset.messages
    assert cache_key(MODEL, request) == cache_key(MODEL, unset)


def test_reference_only_label_appears_only_next_to_an_editable_definition():
    other = Definition("other_val", "variable", "lib/other.c", 1, 1, "int other_val;")
    source = decode_source(
        "src/calc.c", b"int divide(int a, int b)\n{\n    return a / drv_scale(other_val);\n}\n"
    )
    request = make_request(
        RTE, [finding(line=3)], source, None, [DRV_DEF, other], {"lib/drv.c": DRV}
    )

    content = request.messages[1]["content"]
    assert "// reference only: lib/other.c line 1\nint other_val;" in content
    assert "// editable: lib/drv.c lines 1-4" in content


def test_misra_never_marks_definitions_editable():
    request = make_request(
        MISRA, [misra_finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV}
    )

    content = request.messages[1]["content"]
    assert "editable" not in content
    assert "for reference only" in content


def test_fix_in_another_file_is_located_there_and_shown_in_the_diff():
    handler, _ = scripted(answer(("fix", REASON), edits=[DRV_FIX]))

    _, (result,) = ask_with(handler)

    (edit,) = result.edits
    assert edit.file == "lib/drv.c"
    assert apply_edits(DRV.text, [replace(edit, file="")]) == DRV.text.replace("v - 1", "v + 1")
    assert "--- a/lib/drv.c" in result.diff and "+    return v + 1;" in result.diff


def test_local_and_other_file_edits_share_one_diff_local_first():
    local = {
        "file": "",
        "original": "    return a / drv_scale(b);",
        "replacement": "    return a / (drv_scale(b) ? drv_scale(b) : 1);",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[DRV_FIX, local]))

    _, (result,) = ask_with(handler)

    assert [edit.file for edit in result.edits] == ["", "lib/drv.c"]
    assert result.diff.index("--- a/src/calc.c") < result.diff.index("--- a/lib/drv.c")


def test_edit_in_the_checked_file_outside_the_sent_range_uses_its_definition():
    # 검사 파일 안의 다른 함수(보낸 범위 밖)도 정의로 붙었으면 고칠 수 있고, file은 비운다
    text = (
        "int helper(int v)\n{\n    return v - 1;\n}\n"
        + "\n" * 2100
        + "int divide(int a, int b)\n{\n    return a / helper(b);\n}\n"
    )
    source = decode_source("src/calc.c", text.encode())
    helper = Definition(
        "helper", "function", "src/calc.c", 1, 4, "int helper(int v)\n{\n    return v - 1;\n}"
    )
    fix = {
        "file": "src/calc.c",
        "original": "    return v - 1;",
        "replacement": "    return v + 1;",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[fix]))
    line = text.count("\n", 0, text.index("return a / helper")) + 1

    request = make_request(
        RTE, [finding(line=line)], source, None, [helper], {"src/calc.c": source}
    )
    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.edits[0].file == "" and result.edits[0].start == text.index("    return v - 1;")


@pytest.mark.parametrize(
    "bad",
    [
        {"file": "lib/unknown.c", "original": "    return v - 1;", "replacement": "x"},
        {"file": "lib/drv.c", "original": "    return a / drv_scale(b);", "replacement": "x"},
    ],
)
def test_other_file_edit_outside_what_was_shown_is_asked_again(bad):
    handler, sent = scripted(
        answer(("fix", REASON), edits=[bad]), answer(("fix", REASON), edits=[DRV_FIX])
    )

    _, (result,) = ask_with(handler)

    assert len(sent) == 2
    assert result.edits[0].file == "lib/drv.c"


def test_clipped_definition_can_be_edited_only_in_the_shown_lines():
    body = "\n".join(f"    v += {i};" for i in range(MAX_DEFINITION_LINES + 10))
    text = f"int drv_scale(int v)\n{{\n{body}\n    return v;\n}}\n"
    drv = decode_source("lib/drv.c", text.encode())
    lines = text.split("\n")
    clipped = Definition(
        "drv_scale",
        "function",
        "lib/drv.c",
        1,
        len(lines) - 1,
        "\n".join(lines[:MAX_DEFINITION_LINES]) + "\n... (more)",
    )
    hidden = {"file": "lib/drv.c", "original": "    return v;", "replacement": "    return v + 1;"}
    shown = {"file": "lib/drv.c", "original": "    v += 0;", "replacement": "    v += 100;"}
    handler, sent = scripted(
        answer(("fix", REASON), edits=[hidden]), answer(("fix", REASON), edits=[shown])
    )

    _, (result,) = ask_with(handler, definitions=(clipped,), files={"lib/drv.c": drv})

    assert len(sent) == 2
    assert "v += 100;" in result.diff


def test_editable_file_contents_are_part_of_the_cache_key():
    changed = decode_source("lib/drv.c", DRV.text.encode() + b"int other;\n")
    a = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": DRV})
    b = make_request(RTE, [finding(line=3)], USE_DRV, None, [DRV_DEF], {"lib/drv.c": changed})

    assert cache_key(MODEL, a) != cache_key(MODEL, b)


HELPER_TEXT = "int helper(int v)\n{\n    v = v - 1;\n    return v;\n}"
HELPER = Definition("helper", "function", "src/calc.c", 1, 5, HELPER_TEXT)
TAIL = "int divide(int a, int b)\n{\n    v = v - 1;\n    return a / helper(b);\n}\n"
LONG_TEXT = HELPER_TEXT + "\n" + "\n" * 2100 + TAIL
LONG_SOURCE = decode_source("src/calc.c", LONG_TEXT.encode())


def ask_long(fix):
    # 2000줄이 넘어 divide만 보내고, 같은 파일의 helper는 정의로 붙는다. 같은 줄이 양쪽에 있다
    handler, _ = scripted(answer(("fix", REASON), edits=[fix]))
    line = LONG_TEXT.count("\n", 0, LONG_TEXT.index("return a / helper")) + 1
    files = {"src/calc.c": LONG_SOURCE}
    request = make_request(RTE, [finding(line=line)], LONG_SOURCE, None, [HELPER], files)
    assert (request.first, request.last) != (1, len(LONG_TEXT.split("\n")))
    (result,) = propose_fixes(make_client(handler), MODEL, request)
    return result


def test_checked_file_edit_found_in_the_sent_function_does_not_go_to_its_definition():
    fix = {"file": "", "original": "    v = v - 1;", "replacement": "    v = v - 2;"}

    (edit,) = ask_long(fix).edits

    assert edit.file == "" and edit.start == LONG_TEXT.rindex("    v = v - 1;")


def test_checked_file_edit_only_in_its_definition_is_found_there():
    fix = {"file": "src/calc.c", "original": "    return v;", "replacement": "    return v + 1;"}

    (edit,) = ask_long(fix).edits

    assert edit.file == "" and edit.start == LONG_TEXT.index("    return v;")


def test_stored_copy_of_the_checked_file_does_not_replace_the_sent_source():
    # 작업에 저장된 원문이 보낸 원문과 달라도 검사 파일은 보낸 원문 기준이다
    stale = decode_source(
        "src/calc.c", LONG_TEXT.replace(TAIL, TAIL.replace("v - 1", "v - 3")).encode()
    )
    handler, _ = scripted(
        answer(
            ("fix", REASON),
            edits=[{"file": "", "original": "    v = v - 1;", "replacement": "    v = 0;"}],
        )
    )
    line = LONG_TEXT.count("\n", 0, LONG_TEXT.index("return a / helper")) + 1
    request = make_request(
        RTE, [finding(line=line)], LONG_SOURCE, None, [HELPER], {"src/calc.c": stale}
    )

    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.edits[0].start == LONG_TEXT.rindex("    v = v - 1;")


GUARD = {
    "original": "    return a / b;",
    "replacement": "    if (b == 0) return 0;\n    return a / b;",
}


@pytest.mark.parametrize(("kind", "make"), [(RTE, finding), (MISRA, misra_finding)])
def test_same_insertion_given_twice_is_applied_once(kind, make):
    handler, _ = scripted(answer(("fix", REASON), edits=[GUARD, dict(GUARD)]))

    (result,) = ask(handler, [make(line=3)], kind=kind)

    assert apply_edits(SOURCE.text, result.edits).count("if (b == 0)") == 1
    assert result.diff.count("+    if (b == 0) return 0;") == 1


@pytest.mark.parametrize("name", ["LIB\\Drv.c", "./lib/drv.c", "Lib/DRV.C"])
def test_other_file_name_is_matched_loosely_and_stored_as_the_job_name(name):
    handler, sent = scripted(answer(("fix", REASON), edits=[{**DRV_FIX, "file": name}]))

    _, (result,) = ask_with(handler)

    assert len(sent) == 1
    assert [edit.file for edit in result.edits] == ["lib/drv.c"]


def test_checked_file_name_is_matched_loosely_and_stored_empty():
    local = {"file": "SRC\\Calc.c", "original": "    return a / drv_scale(b);", "replacement": "x"}
    handler, sent = scripted(answer(("fix", REASON), edits=[local]))

    _, (result,) = ask_with(handler)

    assert len(sent) == 1
    assert [edit.file for edit in result.edits] == [""]


CALC = decode_source("app/calc.c", b"int divide(int a, int b)\n{\n    return a / b;\n}\n")
MAIN = decode_source(
    "app/main.c",
    b"int update(int sum)\n{\n    int n = read_count();\n    return divide(sum, n);\n}\n",
)
UPDATE = Definition("divide", "caller", "app/main.c", 1, 5, MAIN.text.rstrip("\n"))
FILES = {"app/calc.c": CALC, "app/main.c": MAIN}


NAME_COUNT = Definition("divide", "caller_ref", "", 0, 0, "3")


def caller_request(kind=RTE, definitions=(UPDATE,), files=FILES):
    return make_request(kind, [finding(line=3)], CALC, None, definitions, files)


def test_rte_request_carries_editable_callers():
    request = caller_request()

    content = request.messages[1]["content"]
    assert "Callers of divide(), for the values that reach its parameters." in content
    assert (
        "// editable caller: app/main.c lines 1-5 (in update())\n    1| int update(int sum)"
        in content
    )
    assert request.callers == (UPDATE,)
    assert "app/main.c" in request.files


def test_no_caller_section_without_fragments_files_or_for_misra():
    plain = make_request(RTE, [finding(line=3)], CALC)

    for request in (
        caller_request(definitions=()),
        caller_request(files={"app/calc.c": CALC}),
        make_request(MISRA, [misra_finding(line=3)], CALC, None, (UPDATE,), FILES),
    ):
        assert "Callers of" not in request.messages[1]["content"]
        assert request.callers == ()
    assert caller_request(definitions=()).messages == plain.messages
    assert caller_request(definitions=(NAME_COUNT,)).messages == plain.messages


def test_caller_section_says_how_many_call_sites_are_hidden():
    many = tuple(Definition("divide", "caller", "app/main.c", 1, 5, "x") for _ in range(1)) + tuple(
        Definition("divide", "caller", f"app/m{i}.c", 1, 5, "x") for i in range(5)
    )
    files = {**FILES, **{f"app/m{i}.c": replace(MAIN, name=f"app/m{i}.c") for i in range(5)}}

    request = caller_request(definitions=many, files=files)

    assert "2 more call sites are not shown." in request.messages[1]["content"]
    assert "At least" not in request.messages[1]["content"]
    assert len(request.callers) == 4


def test_fix_in_a_caller_is_located_in_its_fragment():
    guard = {
        "file": "app/main.c",
        "original": "    int n = read_count();",
        "replacement": "    int n = read_count();\n    if (n == 0) {\n        n = 1;\n    }",
    }
    handler, _ = scripted(answer(("fix", REASON), edits=[guard]))

    (result,) = propose_fixes(make_client(handler), MODEL, caller_request())

    assert [edit.file for edit in result.edits] == ["app/main.c"]
    assert "--- a/app/main.c" in result.diff and "+    if (n == 0) {" in result.diff


def test_fix_in_a_caller_file_outside_its_fragment_is_asked_again():
    fragment = Definition("divide", "caller", "app/main.c", 4, 5, "    return divide(sum, n);\n}")
    outside = {"file": "app/main.c", "original": "    int n = read_count();", "replacement": "x"}
    inside = {
        "file": "app/main.c",
        "original": "    return divide(sum, n);",
        "replacement": "    return divide(sum, n ? n : 1);",
    }
    handler, sent = scripted(
        answer(("fix", REASON), edits=[outside]), answer(("fix", REASON), edits=[inside])
    )

    (result,) = propose_fixes(make_client(handler), MODEL, caller_request(definitions=(fragment,)))

    assert len(sent) == 2
    assert "n ? n : 1" in result.diff


def test_caller_section_says_at_least_when_the_stored_callers_hit_the_cap():
    names = [f"app/m{i}.c" for i in range(MAX_STORED_CALLERS)]
    stored = tuple(Definition("divide", "caller", name, 1, 5, "x") for name in names)
    files = {**FILES, **{name: replace(MAIN, name=name) for name in names}}

    request = caller_request(definitions=stored, files=files)

    hidden = MAX_STORED_CALLERS - 4
    assert f"At least {hidden} more call sites are not shown." in request.messages[1]["content"]


def test_caller_section_counts_the_name_used_without_a_call():
    request = caller_request(definitions=(UPDATE, NAME_COUNT))

    content = request.messages[1]["content"]
    assert "Its name also appears without a call in 3 places (possibly function pointers)." in (
        content
    )
    assert request.callers == (UPDATE,) and request.definitions == ()


def test_callers_already_in_the_sent_code_are_not_repeated():
    calc = decode_source(
        "app/calc.c",
        b"int divide(int a, int b)\n{\n    return a / b;\n}\n"
        b"int half(int v)\n{\n    return divide(v, 2);\n}\n",
    )
    home = tuple(Definition("divide", "caller", "app/calc.c", n, 8, "x") for n in (5, 6, 7, 8))
    files = {**FILES, "app/calc.c": calc}

    request = make_request(RTE, [finding(line=3)], calc, None, (*home, UPDATE), files)

    assert request.callers == (UPDATE,)
    assert "not shown" not in request.messages[1]["content"]
