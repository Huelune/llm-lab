import httpx2
from fake_llm import models

from llm_lab.client import get_client
from llm_lab.config import Settings

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
