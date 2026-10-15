"""LLM 연결 값과 저장소 (connections.py)."""

import json

import pytest

from llm_lab.config import ConfigError, Settings
from llm_lab.connections import (
    ENV_NAME,
    Connection,
    ConnectionStore,
    connection_from_form,
    connection_from_settings,
)

FORM = {
    "name": "게이트웨이 B",
    "client_kind": "openai",
    "base_url": "https://llm.test/v1/",
    "api_key": "secret-key",
    "model": "secret-model",
    "project_id": "",
    "project_header": "",
    "api_version": "",
    "timeout": "",
    "verify_ssl": "true",
    "enable_thinking": "",
}


def test_form_is_checked_with_the_env_rules():
    connection = connection_from_form(FORM)

    assert connection.base_url == "https://llm.test/v1"  # 끝의 / 정리는 settings_from_env 규칙
    assert connection.api_key == "secret-key" and connection.timeout == 60.0
    with pytest.raises(ConfigError, match="LLM_TIMEOUT"):
        connection_from_form({**FORM, "timeout": "abc"})
    with pytest.raises(ConfigError, match="이름"):
        connection_from_form({**FORM, "name": " "})
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        connection_from_form({**FORM, "client_kind": "azure", "api_key": ""})


def test_form_with_only_required_keys_is_accepted():
    minimal = {k: FORM[k] for k in ("name", "client_kind", "base_url", "api_key", "model")}

    assert connection_from_form(minimal).verify_ssl is True


def test_blank_secrets_on_edit_keep_the_saved_ones():
    saved = connection_from_form({**FORM, "project_id": "secret-project"})

    edited = connection_from_form(
        {**FORM, "api_key": "", "project_id": "", "model": "m2"}, keep=saved
    )

    assert edited.api_key == "secret-key" and edited.project_id == "secret-project"
    assert edited.model == "m2"
    assert "secret" not in repr(edited).replace("secret-model", "")


def test_store_adds_selects_edits_and_deletes(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    a = connection_from_form({**FORM, "name": "A"})
    b = connection_from_form({**FORM, "name": "B"})

    store.save(a)
    store.save(b)
    assert [c.name for c in store.list()] == ["A", "B"]
    assert store.current().name == "A"
    store.select("B")
    assert ConnectionStore(path).current().name == "B"  # 파일에 남는다

    store.save(connection_from_form({**FORM, "name": "B2"}), original="B")
    assert store.current().name == "B2"
    with pytest.raises(ConfigError, match="같은 이름"):
        store.save(connection_from_form({**FORM, "name": "A"}), original="B2")
    with pytest.raises(ConfigError, match="현재 연결"):
        store.delete("B2")
    store.delete("A")
    assert [c.name for c in store.list()] == ["B2"]
    assert json.loads(path.read_text(encoding="utf-8"))["current"] == "B2"


def test_env_connection_is_listed_first_and_read_only(tmp_path):
    settings = Settings(base_url="https://env.test/v1", api_key="secret-env", model="m")
    env = connection_from_settings(ENV_NAME, settings, read_only=True)
    store = ConnectionStore(tmp_path / "connections.json", env)

    assert store.current() is env
    store.save(connection_from_form({**FORM, "name": "A"}))
    assert [c.name for c in store.list()] == [ENV_NAME, "A"]
    assert store.current().name == ENV_NAME
    with pytest.raises(ConfigError, match=".env"):
        store.save(connection_from_form({**FORM, "name": ENV_NAME}), original=ENV_NAME)
    with pytest.raises(ConfigError, match=".env"):
        store.delete(ENV_NAME)
    assert ENV_NAME not in (tmp_path / "connections.json").read_text(encoding="utf-8")


def test_broken_file_leaves_an_error_kind_and_an_empty_list(tmp_path):
    path = tmp_path / "connections.json"
    path.write_text("{not json", encoding="utf-8")

    store = ConnectionStore(path)

    assert store.error == "JSONDecodeError"
    assert store.list() == [] and store.current() is None


def test_connection_settings_keep_server_options():
    base = Settings(base_url="", api_key="", model="", workers=8, follow_ups=1)
    connection = connection_from_form(FORM)

    settings = connection.settings(base)

    assert settings.workers == 8 and settings.follow_ups == 1
    assert settings.model == "secret-model" and settings.api_key == "secret-key"
    assert connection.fingerprint() != connection_from_form({**FORM, "model": "x"}).fingerprint()


def test_saved_file_is_utf8_and_has_no_secrets_in_repr(tmp_path):
    path = tmp_path / "sub" / "connections.json"
    store = ConnectionStore(path)

    store.save(connection_from_form(FORM))

    assert "게이트웨이 B" in path.read_text(encoding="utf-8")
    assert isinstance(store.list()[0], Connection)
