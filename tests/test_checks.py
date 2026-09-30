import httpx2
import openai
import pytest
from fake_llm import (
    ISSUE_JSON,
    MODEL,
    completion,
    error,
    healthy_server,
    make_client,
    models,
    request_json,
    stream,
    tool_call,
)

from llm_lab.probe import check_chat, check_json, check_models, check_stream, check_tools


def test_models_ok_when_configured_model_listed():
    result = check_models(make_client(lambda request: models("other", MODEL)), MODEL)

    assert result.status == "ok"
    assert "모델 2개" in result.detail


def test_models_partial_when_configured_model_missing():
    result = check_models(make_client(lambda request: models("other")), MODEL)

    assert result.status == "partial"
    assert "LLM_MODEL" in result.detail
    assert "other" in result.detail


def test_models_skip_when_endpoint_not_implemented():
    result = check_models(make_client(lambda request: error(404, "not found")), MODEL)

    assert result.status == "skip"


def test_chat_ok_reports_tokens():
    result = check_chat(make_client(healthy_server), MODEL)

    assert result.status == "ok"
    assert "'pong'" in result.detail
    assert "in 12 / out 3" in result.detail


def test_chat_without_usage_says_no_token_info():
    result = check_chat(make_client(lambda request: completion("pong", usage=False)), MODEL)

    assert result.status == "ok"
    assert "토큰 정보 없음" in result.detail


def test_chat_empty_content_fails():
    result = check_chat(make_client(lambda request: completion("  ")), MODEL)

    assert result.status == "fail"


def test_chat_does_not_send_max_tokens():
    sent: list[dict] = []

    def handler(request):
        sent.append(request_json(request))
        return completion("pong")

    check_chat(make_client(handler), MODEL)

    assert "max_tokens" not in sent[0]
    assert "max_completion_tokens" not in sent[0]


def test_stream_ok_counts_chunks():
    result = check_stream(make_client(healthy_server), MODEL)

    assert result.status == "ok"
    assert "청크 2개" in result.detail
    assert result.raw == "pong"


def test_stream_without_content_fails():
    result = check_stream(make_client(lambda request: stream()), MODEL)

    assert result.status == "fail"


def test_tools_ok_with_valid_call():
    result = check_tools(make_client(healthy_server), MODEL)

    assert result.status == "ok"
    assert "line=5" in result.detail


def test_tools_fail_when_model_answers_in_text():
    result = check_tools(make_client(lambda request: completion("There is a bug")), MODEL)

    assert result.status == "fail"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("not json", "JSON이 아님"),
        ("[1, 2]", "객체가 아님"),
        ('{"file": "stats.py", "line": 5}', "severity, message"),
    ],
)
def test_tools_partial_when_arguments_broken(arguments, expected):
    client = make_client(lambda request: completion(tool_calls=[tool_call(arguments)]))

    result = check_tools(client, MODEL)

    assert result.status == "partial"
    assert expected in result.detail


def test_tools_unsupported_raises_bad_request():
    # 400은 run_check가 '기능 미지원'으로 분류한다
    with pytest.raises(openai.BadRequestError):
        check_tools(make_client(lambda request: error(400, "tools not supported")), MODEL)


def test_json_ok_with_json_schema():
    result = check_json(make_client(healthy_server), MODEL)

    assert result.status == "ok"


def fallback_server(schema_response: httpx2.Response, object_response: httpx2.Response):
    def handler(request):
        kind = request_json(request)["response_format"]["type"]
        return schema_response if kind == "json_schema" else object_response

    return handler


def test_json_partial_when_only_json_object_works():
    handler = fallback_server(error(400, "json_schema unsupported"), completion(ISSUE_JSON))

    result = check_json(make_client(handler), MODEL)

    assert result.status == "partial"
    assert "json_schema 미지원 → json_object 성공" in result.detail


def test_json_partial_when_schema_ignored():
    handler = fallback_server(completion("plain text"), completion(ISSUE_JSON))

    result = check_json(make_client(handler), MODEL)

    assert result.status == "partial"
    assert "스키마와 다름" in result.detail


def test_json_fail_when_both_rejected():
    handler = fallback_server(error(400, "no schema"), error(400, "no json mode"))

    result = check_json(make_client(handler), MODEL)

    assert result.status == "fail"
    assert "no json mode" in result.detail


def test_json_fail_when_json_object_returns_text():
    handler = fallback_server(error(400, "no schema"), completion("not json"))

    result = check_json(make_client(handler), MODEL)

    assert result.status == "fail"
