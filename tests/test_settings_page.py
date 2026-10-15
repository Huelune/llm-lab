"""설정 페이지와 위쪽 막대 (web + pages)."""

from fake_llm import MODEL, make_client, scripted
from fastapi.testclient import TestClient
from rte_fixtures import fix_answer

from llm_lab.config import Settings
from llm_lab.connections import ENV_NAME, ConnectionStore, connection_from_settings
from llm_lab.store import Store
from llm_lab.web import create_app
from llm_lab.worker import Worker

FORM = {
    "name": "게이트웨이 B",
    "client_kind": "openai",
    "base_url": "https://llm.test/v1",
    "api_key": "secret-key-123",
    "model": "model-b",
    "project_id": "secret-project",
    "project_header": "",
    "api_version": "",
    "timeout": "",
    "verify_ssl": "true",
    "enable_thinking": "",
}


def service(tmp_path, env=True):
    store = Store(tmp_path / "fixer.db")
    base = Settings(base_url="https://env.test/v1", api_key="secret-env", model=MODEL)
    connections = ConnectionStore(
        tmp_path / "connections.json",
        connection_from_settings(ENV_NAME, base, read_only=True) if env else None,
    )
    handler, _ = scripted(fix_answer())
    worker = Worker(
        store,
        None,
        "",
        lambda task: None,
        connections=connections,
        base_settings=base,
        make_client=lambda settings: make_client(handler),
    )
    client = TestClient(create_app(store, worker, connections), base_url="http://127.0.0.1")
    return client, connections


def test_add_select_and_show_the_current_connection_without_secrets(tmp_path):
    client, connections = service(tmp_path)

    assert client.post("/settings/save", data=FORM, follow_redirects=False).status_code == 303
    client.post("/settings/select", data={"name": "게이트웨이 B"})
    html = client.get("/settings").text
    home = client.get("/").text

    assert "게이트웨이 B" in html and "model-b" in html and "현재 연결" in html
    assert "연결: 게이트웨이 B" in home
    assert "model-b" not in home  # 위쪽 막대에는 모델 이름이 없다
    for text in (html, home):
        assert "secret" not in text


def test_edit_with_blank_key_keeps_it(tmp_path):
    client, connections = service(tmp_path)
    client.post("/settings/save", data=FORM)

    form = client.get("/settings", params={"edit": "게이트웨이 B"}).text
    client.post(
        "/settings/save",
        data={**FORM, "api_key": "", "project_id": "", "model": "m2", "original": "게이트웨이 B"},
    )

    assert 'name="original" value="게이트웨이 B"' in form and "secret" not in form
    saved = connections.get("게이트웨이 B")
    assert saved.api_key == "secret-key-123" and saved.model == "m2"


def test_invalid_form_shows_the_reason(tmp_path):
    client, _ = service(tmp_path)

    response = client.post("/settings/save", data={**FORM, "timeout": "abc"})

    assert response.status_code == 200 and "LLM_TIMEOUT" in response.text
    assert "secret" not in response.text


def test_env_connection_has_no_edit_or_delete(tmp_path):
    client, _ = service(tmp_path)

    html = client.get("/settings").text

    assert ".env 파일에서 고침" in html
    assert client.post("/settings/delete", data={"name": ENV_NAME}).status_code == 200


def test_no_connection_blocks_analysis_before_reading_the_excel(tmp_path):
    client, _ = service(tmp_path, env=False)

    home = client.get("/").text
    response = client.post(
        "/results", files={"excel": ("x.xlsx", b"not an excel")}, data={"source_dir": str(tmp_path)}
    )

    assert "연결: 없음" in home and "설정에서 LLM 연결을 추가하세요" in home
    assert response.status_code == 400 and "LLM 연결이 없음" in response.text
