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
