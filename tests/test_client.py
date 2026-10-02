import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx2
import openai
import pytest
from fake_llm import completion, models, request_json

from llm_lab.client import get_client
from llm_lab.config import Settings
from llm_lab.probe import SSL_ERROR, classify_error

CERTS = Path(__file__).parent / "certs"

SETTINGS = Settings(base_url="https://llm.test/v1", api_key="company-key", model="m1", timeout=15.0)


def test_get_client_uses_settings(monkeypatch):
    # 개인 OpenAI 키가 환경변수에 있어도 회사 설정이 우선해야 한다
    monkeypatch.setenv("OPENAI_API_KEY", "personal-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    client = get_client(SETTINGS)

    assert client.api_key == "company-key"
    assert str(client.base_url) == "https://llm.test/v1/"
    assert client.timeout == 15.0


def test_openai_env_headers_do_not_reach_the_wire(tmp_path, monkeypatch):
    # .env 없이 LLM_*만으로 설정하고(load_settings 경로), 개인 OPENAI_* 헤더 변수를 심어 둔다
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "company-key")
    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer sk-personal")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-personal")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj_personal")
    sent: list[httpx2.Headers] = []

    def handler(request):
        sent.append(request.headers)
        return models("m1")

    client = get_client().with_options(
        max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    client.models.list()

    assert sent[0]["authorization"] == "Bearer company-key"
    assert "openai-organization" not in sent[0]
    assert "openai-project" not in sent[0]


def capture():
    """요청을 기록하는 MockTransport http 클라이언트와 기록 목록."""
    sent: list[httpx2.Request] = []

    def handler(request):
        sent.append(request)
        if request.url.path.endswith("/models"):
            return models("m1")
        return completion("pong")

    return httpx2.Client(transport=httpx2.MockTransport(handler)), sent


def test_blank_api_key_sends_no_authorization(monkeypatch):
    # 개인 OpenAI 키·헤더가 환경변수에 있어도 키 없는 회사 서버로 새지 않아야 한다
    monkeypatch.setenv("OPENAI_API_KEY", "personal-key")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer sk-personal")
    http_client, sent = capture()
    settings = Settings(base_url="https://llm.test/v1", api_key="", model="m1")

    client = get_client(settings).with_options(max_retries=0, http_client=http_client)
    client.models.list()

    assert "authorization" not in sent[0].headers


def test_enable_thinking_is_sent_on_every_chat_request():
    http_client, sent = capture()
    settings = Settings(
        base_url="https://llm.test/v1", api_key="k", model="m1", enable_thinking=False
    )

    client = get_client(settings).with_options(max_retries=0, http_client=http_client)
    client.models.list()
    client.chat.completions.create(model="m1", messages=[{"role": "user", "content": "hi"}])
    client.chat.completions.create(
        model="m1", messages=[{"role": "user", "content": "hi"}], stream=False, temperature=0
    )

    assert sent[0].content == b""
    for request in sent[1:]:
        assert request_json(request)["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_unset_sends_no_chat_template_kwargs():
    http_client, sent = capture()

    client = get_client(SETTINGS).with_options(max_retries=0, http_client=http_client)
    client.chat.completions.create(model="m1", messages=[{"role": "user", "content": "hi"}])

    assert "chat_template_kwargs" not in request_json(sent[0])


@pytest.fixture
def self_signed_server(monkeypatch):
    """자체 서명 인증서로 /v1/models에 응답하는 로컬 HTTPS 서버의 주소."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    body = json.dumps(
        {"object": "list", "data": [{"id": "m1", "object": "model", "created": 0, "owned_by": "x"}]}
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # 인증서를 거부한 클라이언트의 핸드셰이크 실패는 예상된 동작이다

    server = QuietServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERTS / "localhost-cert.pem", CERTS / "localhost-key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


def test_self_signed_certificate_is_rejected_by_default(self_signed_server):
    settings = Settings(base_url=self_signed_server, api_key="k", model="m1", timeout=5.0)

    with pytest.raises(openai.APIConnectionError) as exc_info:
        get_client(settings).with_options(max_retries=0).models.list()

    assert classify_error(exc_info.value)[0] == SSL_ERROR


def test_verify_ssl_false_accepts_self_signed_certificate(self_signed_server):
    settings = Settings(
        base_url=self_signed_server, api_key="k", model="m1", timeout=5.0, verify_ssl=False
    )

    page = get_client(settings).with_options(max_retries=0).models.list()

    assert [item.id for item in page.data] == ["m1"]
