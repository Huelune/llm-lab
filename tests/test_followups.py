"""정의 되묻기: 지시문·답 형식·요청 (followups.py, make_request)."""

from dataclasses import replace

import pytest
from fake_llm import MODEL, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab.fixer import FIX_SCHEMA_WITH_FILE, cache_key, make_request, propose_fixes
from llm_lab.followups import ASK_RULE, GUESS_RULE, need_names, with_follow_ups, with_need
from llm_lab.kinds import CODEMETRICS, MISRA, RTE
from llm_lab.sources import decode_source

SOURCE = decode_source("calc.c", CALC_C)


@pytest.mark.parametrize("kind", [RTE, MISRA, CODEMETRICS])
def test_every_kind_asks_and_gets_the_rule_before_the_json_rule(kind):
    assert kind.asks_definitions
    prompt = kind.prompt(None)
    assert GUESS_RULE in prompt

    text = with_follow_ups(prompt, 2)

    assert GUESS_RULE not in text and ASK_RULE in text
    assert 'put its exact name in "need", at most 10 names. You can ask 2 times in total.' in text
    assert text.index('"need"') < text.index("Answer with JSON only.")


def test_zero_rounds_keep_the_prompt_and_one_round_is_singular():
    prompt = RTE.prompt(None)
    assert with_follow_ups(prompt, 0) == prompt
    assert "You can ask 1 time in total." in with_follow_ups(prompt, 1)


def test_with_need_adds_a_required_list_and_leaves_the_original():
    schema = with_need(FIX_SCHEMA_WITH_FILE)

    assert schema["properties"]["need"] == {"type": "array", "items": {"type": "string"}}
    assert schema["required"][-1] == "need"
    assert "need" not in FIX_SCHEMA_WITH_FILE["properties"]
    assert "need" not in FIX_SCHEMA_WITH_FILE["required"]


def test_need_names_keeps_identifiers_once_and_at_most_ten():
    data = {"need": ["helper", " helper ", "a b", "x->y", 3, "_ok1", *[f"n{i}" for i in range(12)]]}

    assert need_names(data) == ["helper", "_ok1", *[f"n{i}" for i in range(8)]]
    assert need_names({"need": "helper"}) == []
    assert need_names({}) == []
    assert need_names([]) == []


def test_make_request_without_follow_ups_sends_todays_messages():
    plain = make_request(RTE, [finding()], SOURCE)
    zero = make_request(RTE, [finding()], SOURCE, follow_ups=0)

    assert zero.messages == plain.messages
    assert zero.follow_ups == 0 and zero.asked == ()
    assert zero.schema is RTE.answer_schema
    assert cache_key(MODEL, zero) == cache_key(MODEL, plain)


def test_make_request_with_follow_ups_changes_prompt_schema_and_key():
    plain = make_request(RTE, [finding()], SOURCE)
    asking = make_request(RTE, [finding()], SOURCE, follow_ups=2)

    assert asking.follow_ups == 2
    assert "need" in asking.schema["properties"]
    assert ASK_RULE in asking.messages[0]["content"]
    assert asking.messages[1] == plain.messages[1]
    assert cache_key(MODEL, asking) != cache_key(MODEL, plain)
    once = make_request(RTE, [finding()], SOURCE, follow_ups=1)
    assert cache_key(MODEL, asking) != cache_key(MODEL, once)


def test_kind_that_does_not_ask_ignores_follow_ups():
    kind = replace(RTE, asks_definitions=False)

    request = make_request(kind, [finding()], SOURCE, follow_ups=2)

    assert request.follow_ups == 0
    assert request.schema is RTE.answer_schema


def test_propose_fixes_sends_the_schema_with_need_when_asking():
    handler, sent = scripted(fix_answer())
    request = make_request(RTE, [finding()], SOURCE, follow_ups=2)

    (result,) = propose_fixes(make_client(handler), MODEL, request)

    assert result.decision == "fix"
    assert "need" in sent[0]["response_format"]["json_schema"]["schema"]["properties"]
