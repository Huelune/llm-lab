"""정의 되묻기: 지시문·답 형식·요청 (followups.py, make_request)."""

import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fake_llm import MODEL, make_client, scripted
from rte_fixtures import CALC_C, finding, fix_answer

from llm_lab import followups
from llm_lab.csource import Definition
from llm_lab.fixer import FIX_SCHEMA_WITH_FILE, cache_key, make_request, propose_fixes
from llm_lab.followups import (
    ASK_RULE,
    GUESS_RULE,
    Asked,
    IndexCache,
    ProjectIndex,
    find_asked,
    need_names,
    with_follow_ups,
    with_need,
)
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

    # "a b"와 "x->y"는 마지막 이름만 남는다
    assert need_names(data) == ["helper", "b", "y", "_ok1", *[f"n{i}" for i in range(6)]]
    assert need_names({"need": "helper"}) == []
    assert need_names({}) == []
    assert need_names([]) == []


def test_need_names_takes_the_name_out_of_calls_and_tags():
    data = {
        "need": ["deep_scale()", "struct cfg", "deep_scale", "Rte_Read_x (uint8 *v)", "->", "  "]
    }

    assert need_names(data) == ["deep_scale", "cfg", "Rte_Read_x"]


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


API = Definition("api_get", "macro", "inc/api.h", 3, 3, "#define api_get(x) impl_get(x)")
IMPL = Definition(
    "impl_get", "function", "gen/impl.c", 10, 13, "int impl_get(int x)\n{\n    return x;\n}"
)
HERE = Definition("divide", "function", "calc.c", 1, 4, "int divide(int a, int b)\n{\n}\n")
OTHER = Definition("other_fn", "function", "gen/o.c", 1, 3, "int other_fn(void)\n{\n}")


def index_of(*definitions: Definition) -> ProjectIndex:
    by_name: dict[str, list[Definition]] = {}
    for definition in definitions:
        by_name.setdefault(definition.name, []).append(definition)
    return ProjectIndex(Path("."), {name: tuple(found) for name, found in by_name.items()})


def test_find_asked_sends_new_definitions_and_follows_aliases():
    asked = find_asked(["api_get"], index_of(API, IMPL), "calc.c", 1, 4, ())

    assert asked.definitions == (API, IMPL)
    assert asked.missing == asked.shown == asked.later == ()


def test_find_asked_sorts_names_into_missing_and_shown():
    asked = find_asked(
        ["nowhere", "divide", "impl_get"], index_of(HERE, IMPL), "calc.c", 1, 4, (IMPL,)
    )

    assert asked.missing == ("nowhere",)
    assert asked.shown == ("divide", "impl_get")  # 보낸 코드 안, 이미 보낸 정의
    assert asked.definitions == ()


def test_alias_already_sent_still_brings_its_target():
    asked = find_asked(["api_get"], index_of(API, IMPL), "calc.c", 1, 4, (API,))

    assert asked.definitions == (IMPL,)
    assert asked.shown == ()


def test_find_asked_stops_at_the_definition_limit(monkeypatch):
    monkeypatch.setattr(followups, "MAX_ASKED_DEFINITIONS", 1)

    asked = find_asked(
        ["impl_get", "other_fn", "nowhere"], index_of(IMPL, OTHER), "calc.c", 1, 4, ()
    )

    assert asked.definitions == (IMPL,)
    assert asked.later == ("other_fn",)
    assert asked.missing == ("nowhere",)


def test_find_asked_stops_at_the_line_limit(monkeypatch):
    monkeypatch.setattr(followups, "MAX_ASKED_LINES", 3)

    asked = find_asked(["other_fn", "impl_get"], index_of(IMPL, OTHER), "calc.c", 1, 4, ())

    assert asked.definitions == (OTHER,)
    assert asked.later == ("impl_get",)


def test_no_index_means_the_names_cannot_be_looked_up():
    assert find_asked(["x"], None, "calc.c", 1, 4, ()) == Asked(unavailable=True)


def test_project_index_reads_the_folder_and_rereads_one_file(tmp_path):
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "impl.c").write_bytes(b"int impl_get(int x)\n{\n    return x;\n}\n")

    index = ProjectIndex.build(tmp_path)

    assert [d.file for d in index.definitions["impl_get"]] == ["gen/impl.c"]
    assert index.source("gen/impl.c").text.startswith("int impl_get")
    assert index.source("gen/none.c") is None


def test_project_index_takes_the_job_text_over_the_disk(tmp_path):
    # 작업이 저장한 글과 디스크 파일이 다르면 (작업 중 패치 등) 줄 번호는 저장한 글을 따른다
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "impl.c").write_bytes(b"\n\nint impl_get(int x)\n{\n    return x;\n}\n")
    (tmp_path / "gen" / "disk.c").write_bytes(b"int disk_only(void)\n{\n}\n")
    texts = {
        "gen/impl.c": "int impl_get(int x)\n{\n    return x;\n}\n",
        "gen/kept.c": "int kept_only(void)\n{\n}\n",  # 디스크에 없는 저장 파일
    }

    index = ProjectIndex.build(tmp_path, texts)

    assert [(d.file, d.first, d.last) for d in index.definitions["impl_get"]] == [
        ("gen/impl.c", 1, 4)
    ]
    assert [d.file for d in index.definitions["kept_only"]] == ["gen/kept.c"]
    assert [d.file for d in index.definitions["disk_only"]] == ["gen/disk.c"]


def test_index_cache_builds_each_job_once_and_keeps_two(tmp_path):
    built: list[Path] = []
    cache = IndexCache(build=lambda root, texts: built.append(root) or ProjectIndex(root, {}))

    threads = [threading.Thread(target=cache.get, args=("1", str(tmp_path))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(built) == 1

    cache.get("2", str(tmp_path))
    cache.get("3", str(tmp_path))
    cache.get("1", str(tmp_path))  # 2개만 두므로 1은 다시 만든다
    assert len(built) == 4
    assert cache.get("9", str(tmp_path / "gone")) is None
    assert cache.get("8", "") is None


def test_index_cache_passes_the_job_texts_to_the_build(tmp_path):
    seen: list[dict] = []
    cache = IndexCache(build=lambda root, texts: seen.append(texts) or ProjectIndex(root, {}))

    cache.get("1", str(tmp_path), {"a.c": "int a;"})
    cache.get("2", str(tmp_path))

    assert seen == [{"a.c": "int a;"}, {}]
