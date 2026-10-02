import io
import ssl
import sys

import httpx2
from fake_llm import API_KEY, MODEL, completion, error, healthy_server, make_client, request_kind

from llm_lab import probe
from llm_lab.config import Settings

SETTINGS = Settings(base_url="https://llm.test/v1", api_key=API_KEY, model=MODEL)


def run(handler, capsys, **kwargs) -> tuple[int, str]:
    code = probe.run_probe(make_client(handler), SETTINGS, **kwargs)
    return code, capsys.readouterr().out


def test_healthy_server_passes_everything(capsys):
    code, out = run(healthy_server, capsys)

    assert code == 0
    assert "https://llm.test/v1 (model: m1)" in out
    for name in ("연결·모델 목록", "기본 채팅", "스트리밍", "Tool calling", "JSON 출력"):
        assert name in out
    assert "요약: 지원 5 / 부분 지원 0 / 미지원 0 / 건너뜀 0" in out


def test_api_key_never_printed_even_verbose(capsys):
    code, out = run(healthy_server, capsys, verbose=True)

    assert code == 0
    assert '"pong"' in out  # verbose 원문이 실제로 출력됐는지
    assert API_KEY not in out


def test_fatal_error_after_chat_success_keeps_exit_code_zero(capsys):
    # spec: 기본 채팅이 성공했다면 이후 단계에서 중단되어도 종료 코드는 0
    def handler(request):
        if request_kind(request) == "stream":
            raise httpx2.ReadTimeout("timed out", request=request)
        return healthy_server(request)

    code, out = run(handler, capsys)

    assert code == 0
    assert "[3] 스트리밍" in out
    assert "점검 중단" in out
    assert "[4]" not in out


def test_blank_api_key_does_not_garble_output(capsys):
    # 빈 문자열로 replace하면 모든 글자 사이에 ***가 끼어든다
    settings = Settings(base_url="https://llm.test/v1", api_key="", model=MODEL)

    code = probe.run_probe(make_client(healthy_server), settings)
    out = capsys.readouterr().out

    assert code == 0
    assert "요약: 지원 5 / 부분 지원 0 / 미지원 0 / 건너뜀 0" in out
    assert "***" not in out


def test_header_lists_non_default_options(capsys):
    # 결과 파일만 보고도 어떤 설정으로 점검했는지 알 수 있어야 한다
    settings = Settings(
        base_url="https://llm.test/v1",
        api_key="",
        model=MODEL,
        verify_ssl=False,
        enable_thinking=False,
    )

    probe.run_probe(make_client(healthy_server), settings)
    first_line = capsys.readouterr().out.splitlines()[0]

    assert "model: m1" in first_line
    assert "API 키 없음" in first_line
    assert "인증서 검사 끔" in first_line
    assert "enable_thinking=false" in first_line


def test_header_omits_options_left_at_default(capsys):
    _, out = run(healthy_server, capsys)

    assert out.splitlines()[0] == "회사 LLM 점검: https://llm.test/v1 (model: m1)"


def test_api_key_echoed_by_server_is_masked(capsys):
    # 게이트웨이가 오류 메시지에 키를 되돌려 보내는 경우
    def handler(request):
        if request_kind(request) == "tools":
            return error(400, f"tools rejected for key {API_KEY}")
        return healthy_server(request)

    code, out = run(handler, capsys)

    assert API_KEY not in out
    assert "tools rejected for key ***" in out


def test_unsupported_feature_does_not_stop_later_checks(capsys):
    def handler(request):
        if request_kind(request) == "tools":
            return error(400, "tools not supported")
        return healthy_server(request)

    code, out = run(handler, capsys)

    assert code == 0
    assert "[기능 미지원]" in out
    assert "tools not supported" in out
    assert "[5] JSON 출력" in out


def test_connection_error_aborts_after_first_check(capsys):
    def handler(request):
        raise httpx2.ConnectError("connection refused", request=request)

    code, out = run(handler, capsys)

    assert code == 1
    assert "[네트워크]" in out
    assert "점검 중단" in out
    assert "[2]" not in out


def test_ssl_failure_through_real_client_is_reported_as_ssl(capsys):
    def handler(request):
        cause = ssl.SSLCertVerificationError("certificate verify failed")
        raise httpx2.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]", request=request) from cause

    code, out = run(handler, capsys)

    assert code == 1
    assert "[SSL]" in out


def test_auth_error_aborts(capsys):
    code, out = run(lambda request: error(401, "invalid api key"), capsys)

    assert code == 1
    assert "[인증]" in out
    assert "점검 중단" in out


def test_base_url_without_v1_explains_path(capsys):
    # /v1을 빠뜨리면 모든 경로가 404: 모델 목록은 건너뛰고 채팅에서 /v1 안내
    code, out = run(lambda request: error(404, "not found"), capsys)

    assert code == 1
    assert "⏭ 건너뜀" in out
    assert "[경로]" in out
    assert "끝에 /v1이 있는지" in out  # 헤더 URL의 /v1이 아니라 안내 문구


def test_empty_chat_reply_exits_nonzero(capsys):
    def handler(request):
        if request_kind(request) == "chat":
            return completion("")
        return healthy_server(request)

    code, out = run(handler, capsys)

    assert code == 1
    assert "내용이 비어 있음" in out


def test_non_openai_response_is_reported_without_crashing(capsys):
    # 사내 프록시·SSO가 200으로 HTML 로그인 페이지를 돌려주는 경우
    def handler(request):
        return httpx2.Response(
            200, headers={"content-type": "text/html"}, content=b"<html>login</html>"
        )

    code, out = run(handler, capsys)

    assert code == 1
    assert "[응답 형식]" in out
    assert "[5] JSON 출력" in out


def test_unprocessable_response_does_not_blame_the_server_for_sure(capsys):
    def handler(request):
        return httpx2.Response(
            200, headers={"content-type": "text/html"}, content=b"<html>login</html>"
        )

    _, out = run(handler, capsys)

    assert "응답을 처리하지 못함 - 프록시·로그인 페이지일 수 있음 (" in out
    assert "서버 응답이 OpenAI 형식이 아님" not in out


def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)

    code = probe.main([])

    assert code == 1
    assert "설정 오류" in capsys.readouterr().err


def test_main_survives_cp949_redirected_stdout(tmp_path, monkeypatch):
    # Windows에서 `llm-probe > out.txt`처럼 리다이렉트하면 stdout이 cp949가 된다
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_API_KEY", API_KEY)
    monkeypatch.setenv("LLM_MODEL", MODEL)
    monkeypatch.setattr(probe, "get_client", lambda settings: make_client(healthy_server))
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="cp949"))

    code = probe.main([])

    sys.stdout.flush()
    assert code == 0
    assert "지원" in buffer.getvalue().decode("cp949")
