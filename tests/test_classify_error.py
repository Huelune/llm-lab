import ssl

import httpx2
import openai
import pytest

from llm_lab.probe import classify_error

REQUEST = httpx2.Request("POST", "https://llm.test/v1/chat/completions")


def status_error(cls: type[openai.APIStatusError], status: int, body: object = None):
    return cls("Error", response=httpx2.Response(status, request=REQUEST), body=body)


def connection_error(cause: BaseException | None = None) -> openai.APIConnectionError:
    exc = openai.APIConnectionError(request=REQUEST)
    exc.__cause__ = cause
    return exc


def test_timeout_is_network_error():
    category, hint = classify_error(openai.APITimeoutError(request=REQUEST))

    assert category == "네트워크"
    assert "LLM_TIMEOUT" in hint


def test_plain_connection_error_is_network_error():
    category, hint = classify_error(connection_error(httpx2.ConnectError("refused")))

    assert category == "네트워크"
    assert "LLM_BASE_URL" in hint


def test_ssl_error_anywhere_in_cause_chain_is_ssl_error():
    connect_error = httpx2.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    connect_error.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")

    category, hint = classify_error(connection_error(connect_error))

    assert category == "SSL"
    assert "인증서" in hint


@pytest.mark.parametrize(
    ("cls", "status"),
    [(openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403)],
)
def test_auth_errors(cls, status):
    category, hint = classify_error(status_error(cls, status))

    assert category == "인증"
    assert "LLM_API_KEY" in hint
    assert str(status) in hint


def test_not_found_points_to_v1_and_model():
    category, hint = classify_error(status_error(openai.NotFoundError, 404))

    assert category == "경로"
    assert "/v1" in hint
    assert "LLM_MODEL" in hint


def test_bad_request_shows_server_message():
    exc = status_error(openai.BadRequestError, 400, body={"message": "tools not supported"})

    category, hint = classify_error(exc)

    assert category == "기능 미지원"
    assert "tools not supported" in hint


def test_other_status_is_server_error_with_code():
    exc = status_error(openai.InternalServerError, 503, body={"message": "overloaded"})

    category, hint = classify_error(exc)

    assert category == "서버"
    assert "503" in hint
    assert "overloaded" in hint
