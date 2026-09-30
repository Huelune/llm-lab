import ssl

import httpx2
import openai
import pytest

from llm_lab.probe import _server_message, classify_error

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


def test_timeout_hint_names_root_cause():
    exc = openai.APITimeoutError(request=REQUEST)
    exc.__cause__ = httpx2.ReadTimeout("read timed out")

    _, hint = classify_error(exc)

    assert "(원인: ReadTimeout: read timed out)" in hint


def test_connection_hint_names_root_cause():
    _, hint = classify_error(connection_error(OSError("getaddrinfo failed")))

    assert "getaddrinfo failed" in hint
    assert "(원인: OSError: getaddrinfo failed)" in hint


def test_root_cause_is_followed_through_context_and_survives_cycles():
    inner = OSError("connection refused")
    middle = httpx2.ConnectError("connect failed")
    middle.__context__ = inner  # __cause__ 없이 __context__만 있는 경우
    inner.__context__ = middle  # 순환이 있어도 멈춰야 한다

    _, hint = classify_error(connection_error(middle))

    assert "(원인: OSError: connection refused)" in hint


def test_root_cause_without_message_shows_only_type():
    _, hint = classify_error(connection_error(httpx2.ReadTimeout("")))

    assert hint.endswith("(원인: ReadTimeout)")


def test_ssl_error_anywhere_in_cause_chain_is_ssl_error():
    connect_error = httpx2.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    connect_error.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")

    category, hint = classify_error(connection_error(connect_error))

    assert category == "SSL"
    assert "인증서" in hint


def test_certificate_hint_includes_root_cause():
    # 실제 ssl 예외는 (errno, strerror) 두 인자로 만들어져 str()이 strerror만 보여 준다
    connect_error = httpx2.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    connect_error.__cause__ = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    )

    _, hint = classify_error(connection_error(connect_error))

    assert "인증서 검증 실패" in hint
    assert "(원인: SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate" in hint


@pytest.mark.parametrize(
    "ssl_error",
    [
        ssl.SSLError("WRONG_VERSION_NUMBER"),
        ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"),
    ],
)
def test_generic_ssl_error_is_not_reported_as_certificate_problem(ssl_error):
    # http://로 열린 포트에 https://로 접속하면 인증서가 아니라 프로토콜 오류가 난다
    connect_error = httpx2.ConnectError("[SSL: WRONG_VERSION_NUMBER]")
    connect_error.__cause__ = ssl_error

    category, hint = classify_error(connection_error(connect_error))

    assert category == "SSL"
    assert "http/https" in hint
    assert "인증서 검증 실패" not in hint
    assert "(원인: SSLError: " in hint
    assert "WRONG_VERSION_NUMBER" in hint


@pytest.mark.parametrize(
    ("cls", "status"),
    [(openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403)],
)
def test_auth_errors(cls, status):
    category, hint = classify_error(status_error(cls, status))

    assert category == "인증"
    assert "LLM_API_KEY" in hint
    assert str(status) in hint


def test_auth_error_shows_server_message():
    # 프록시·WAF의 403과 잘못된 키의 403을 구분할 수 있어야 한다
    exc = status_error(openai.PermissionDeniedError, 403, body={"message": "blocked by proxy"})

    _, hint = classify_error(exc)

    assert "LLM_API_KEY" in hint
    assert "서버 메시지: blocked by proxy" in hint


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


def test_long_html_server_message_is_collapsed_and_truncated():
    html = "<html>\n<body>\n" + "<p>blocked by gateway</p>\n" * 60 + "</body>\n</html>"
    assert len(html) > 1000
    exc = openai.BadRequestError(html, response=httpx2.Response(400, request=REQUEST), body=html)

    _, hint = classify_error(exc)

    assert len(hint) <= 300
    assert "\n" not in hint
    assert hint.endswith("…")


def test_server_message_falls_back_to_exception_message_without_body():
    exc = openai.BadRequestError(
        "Error code: 400 - gateway said no",
        response=httpx2.Response(400, request=REQUEST),
        body=None,
    )

    assert _server_message(exc) == "Error code: 400 - gateway said no"


def test_other_status_is_server_error_with_code():
    exc = status_error(openai.InternalServerError, 503, body={"message": "overloaded"})

    category, hint = classify_error(exc)

    assert category == "서버"
    assert "503" in hint
    assert "overloaded" in hint
