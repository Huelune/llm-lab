"""정의 되묻기 대화: propose_fixes가 need를 받아 다음 메시지를 보내고 다시 판단을 받는다."""

import json
from dataclasses import replace

import pytest
from fake_llm import MODEL, completion, make_client, scripted
from misra_fixtures import misra_finding
from rte_fixtures import REASON, finding

from llm_lab.csource import Definition
from llm_lab.fixer import FIX_SCHEMA, FixError, make_request, propose_fixes
from llm_lab.followups import (
    ASKED_EDITABLE,
    ASKED_REFERENCE,
    LAST_LINE,
    LATER_LINE,
    MISSING_LINE,
    SHOWN_LINE,
    UNAVAILABLE_LINE,
    Asked,
)
from llm_lab.kinds import MISRA, RTE
from llm_lab.sources import decode_source

# 받은 정의를 참고만 하는 종류 (MISRA도 이제 고칠 수 있으므로 따로 만든다)
REFERENCE_ONLY = replace(MISRA, edits_definitions=False, answer_schema=FIX_SCHEMA)

USE = decode_source(
    "src/calc.c", b"int divide(int a, int b)\n{\n    return a / deep_scale(b);\n}\n"
)
DEEP = decode_source("lib/deep.c", b"int deep_scale(int v)\n{\n    return v - 1;\n}\n")
DEEP_DEF = Definition("deep_scale", "function", "lib/deep.c", 1, 4, DEEP.text.rstrip("\n"))
DEEP_FIX = {
    "file": "lib/deep.c",
    "original": "    return v - 1;",
    "replacement": "    return v + 1;",
}


def reply(*findings, edits=None, need=()):
    body = {
        "findings": [
            {"number": number, "decision": decision, "reason": reason}
            for number, (decision, reason) in enumerate(findings, start=1)
        ],
        "edits": edits or [],
        "need": list(need),
    }
    return completion(json.dumps(body))


def asking(follow_ups=2):
    return make_request(RTE, [finding(line=3)], USE, None, (), {}, follow_ups=follow_ups)


def test_empty_need_answers_in_one_request():
    handler, sent = scripted(reply(("no_fix", REASON)))
    calls = []

    (result,) = propose_fixes(
        make_client(handler), MODEL, asking(), ask=lambda names, request: calls.append(names)
    )

    assert len(sent) == 1 and calls == []
    assert result.decision == "no_fix"


def test_asked_definition_is_sent_and_its_file_can_be_fixed():
    handler, sent = scripted(
        reply(("manual", REASON), need=["deep_scale"]),
        reply(("fix", REASON), edits=[DEEP_FIX]),
    )
    seen = []

    def ask(names, request):
        seen.append(names)
        return Asked(definitions=(DEEP_DEF,), files={"lib/deep.c": DEEP})

    (result,) = propose_fixes(make_client(handler), MODEL, asking(), ask=ask)

    assert seen == [["deep_scale"]]
    assert [m["role"] for m in sent[1]["messages"]] == ["system", "user", "assistant", "user"]
    follow_up = sent[1]["messages"][-1]["content"]
    assert follow_up.startswith(ASKED_EDITABLE)
    assert "// editable: lib/deep.c lines 1-4\n    1| int deep_scale(int v)" in follow_up
    assert follow_up.endswith("Answer again with the same JSON format. You can ask 1 more time.")
    assert result.decision == "fix"
    assert result.edits[0].file == "lib/deep.c"
    assert "+    return v + 1;" in result.diff


def test_names_not_found_are_called_external_and_the_last_round_says_no_more():
    handler, sent = scripted(
        reply(("manual", REASON), need=["ext_fn"]),
        reply(("manual", REASON), need=["ext_fn"]),
        reply(("manual", REASON), need=["ext_fn"]),  # 마지막 차례의 need는 무시한다
    )
    asked = Asked(missing=("ext_fn",), shown=("divide",), later=("big_fn",))

    (result,) = propose_fixes(
        make_client(handler), MODEL, asking(), ask=lambda names, request: asked
    )

    assert len(sent) == 3
    first = sent[1]["messages"][-1]["content"]
    last = sent[2]["messages"][-1]["content"]
    assert MISSING_LINE + "ext_fn." in first
    assert SHOWN_LINE + "divide." in first
    assert LATER_LINE + "big_fn." in first
    assert first.endswith("You can ask 1 more time.")
    assert last.endswith(LAST_LINE)
    assert result.decision == "manual"


def test_lookup_that_cannot_run_says_so():
    handler, sent = scripted(reply(("manual", REASON), need=["x"]), reply(("manual", REASON)))

    propose_fixes(
        make_client(handler),
        MODEL,
        asking(follow_ups=1),
        ask=lambda names, request: Asked(unavailable=True),
    )

    assert sent[1]["messages"][-1]["content"] == UNAVAILABLE_LINE + "\n\n" + LAST_LINE


def test_bad_json_while_asking_is_retried_once_in_that_round():
    handler, sent = scripted(
        completion("not json"),
        reply(("manual", REASON), need=["x"]),
        reply(("no_fix", REASON)),
    )

    (result,) = propose_fixes(
        make_client(handler),
        MODEL,
        asking(follow_ups=1),
        ask=lambda names, request: Asked(missing=("x",)),
    )

    assert len(sent) == 3
    assert sent[1]["messages"][-1]["content"].startswith("Your answer could not be used")
    assert result.decision == "no_fix"


def test_without_ask_need_is_ignored():
    handler, sent = scripted(reply(("no_fix", REASON), need=["x"]))

    (result,) = propose_fixes(make_client(handler), MODEL, asking())

    assert len(sent) == 1 and result.decision == "no_fix"


def test_reference_only_kind_gets_asked_definitions_for_reference_only():
    handler, sent = scripted(
        reply(("manual", REASON), need=["deep_scale"]), reply(("manual", REASON))
    )
    request = make_request(REFERENCE_ONLY, [misra_finding(line=3)], USE, None, (), {}, follow_ups=1)

    propose_fixes(
        make_client(handler),
        MODEL,
        request,
        ask=lambda names, request: Asked(definitions=(DEEP_DEF,), files={"lib/deep.c": DEEP}),
    )

    follow_up = sent[1]["messages"][-1]["content"]
    assert follow_up.startswith(ASKED_REFERENCE)
    assert "// lib/deep.c lines 1-4\nint deep_scale(int v)" in follow_up
    assert "    1|" not in follow_up


def test_reference_only_kind_cannot_edit_asked_files_even_if_a_caller_passes_them():
    handler, _ = scripted(
        reply(("manual", REASON), need=["deep_scale"]),
        reply(("fix", REASON), edits=[{**DEEP_FIX, "replacement": "x"}]),
        reply(("fix", REASON), edits=[{**DEEP_FIX, "replacement": "x"}]),
    )
    request = make_request(REFERENCE_ONLY, [misra_finding(line=3)], USE, None, (), {}, follow_ups=1)

    with pytest.raises(FixError):
        propose_fixes(
            make_client(handler),
            MODEL,
            request,
            ask=lambda names, request: Asked(definitions=(DEEP_DEF,), files={"lib/deep.c": DEEP}),
        )
