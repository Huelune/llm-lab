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


def test_changed_endpoint_does_not_reuse_the_saved_key():
    # 저장된 키가 바뀐 주소로 새지 않게, 주소나 접속 방식을 바꾸면 키를 다시 받는다
    saved = connection_from_form({**FORM, "project_id": "secret-project"})
    blank = {**FORM, "api_key": "", "project_id": ""}

    for changed in ({"base_url": "https://other.test/v1"}, {"client_kind": "azure"}):
        with pytest.raises(ConfigError, match="키를 다시 입력하세요"):
            connection_from_form({**blank, **changed}, keep=saved)
    # 같은 주소를 다르게 적은 것(끝의 /)은 바뀐 것이 아니다
    same = connection_from_form({**blank, "base_url": "https://llm.test/v1"}, keep=saved)
    assert same.api_key == "secret-key" and same.project_id == "secret-project"
    # 새 키를 적으면 바꿀 수 있고, 저장된 프로젝트 ID는 따라가지 않는다
    moved = connection_from_form(
        {**blank, "base_url": "https://other.test/v1", "api_key": "new-key"}, keep=saved
    )
    assert moved.api_key == "new-key" and moved.project_id == ""


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
    with pytest.raises(ConfigError, match="기본 연결은"):
        store.save(connection_from_form({**FORM, "name": ENV_NAME}), original=ENV_NAME)
    with pytest.raises(ConfigError, match="기본 연결은"):
        store.delete(ENV_NAME)
    file_text = (tmp_path / "connections.json").read_text(encoding="utf-8")
    assert ENV_NAME not in file_text and "secret-env" not in file_text


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


def test_saved_file_is_utf8_in_a_new_folder_and_repr_hides_secrets(tmp_path):
    path = tmp_path / "sub" / "connections.json"
    store = ConnectionStore(path)

    store.save(connection_from_form(FORM))

    assert "게이트웨이 B" in path.read_text(encoding="utf-8")
    assert isinstance(store.list()[0], Connection)
    assert "secret" not in repr(store.list()[0]).replace("secret-model", "")


def test_failed_write_keeps_memory_and_reports_a_config_error(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    store.save(connection_from_form({**FORM, "name": "A"}))
    store.save(connection_from_form({**FORM, "name": "B"}))
    path.unlink()
    path.mkdir()  # os.replace가 실패한다

    with pytest.raises(ConfigError, match="저장하지 못했습니다") as info:
        store.save(connection_from_form({**FORM, "name": "C"}))
    assert str(tmp_path) not in str(info.value)
    with pytest.raises(ConfigError, match="저장하지 못했습니다"):
        store.select("B")
    with pytest.raises(ConfigError, match="저장하지 못했습니다"):
        store.delete("B")

    assert [c.name for c in store.list()] == ["A", "B"] and store.current().name == "A"
    assert not (tmp_path / "connections.json.tmp").exists()


def test_check_connection_reports_kinds_without_raw_text():
    from fake_llm import completion, error, make_client, scripted

    from llm_lab.connections import check_connection

    base = Settings(base_url="", api_key="", model="")
    connection = connection_from_form(FORM)
    ok, _ = scripted(completion("pong"))
    denied, _ = scripted(error(401, "secret detail"))
    missing, _ = scripted(error(404, "secret detail"))

    assert check_connection(connection, base, lambda s: make_client(ok)) == "성공"
    assert check_connection(connection, base, lambda s: make_client(denied)) == "인증 실패"
    assert check_connection(connection, base, lambda s: make_client(missing)) == "주소·모델 오류"


def test_broken_file_is_moved_aside_with_its_bytes(tmp_path):
    path = tmp_path / "connections.json"
    path.write_bytes(b"{not json")

    store = ConnectionStore(path)
    store.save(connection_from_form(FORM))

    assert (tmp_path / "connections.json.bad").read_bytes() == b"{not json"
    assert "connections.json.bad" in store.notice() and "JSONDecodeError" in store.notice()
    assert ConnectionStore(path).get("게이트웨이 B") is not None


def test_good_file_has_no_notice(tmp_path):
    assert ConnectionStore(tmp_path / "connections.json").notice() == ""


def test_env_name_is_refused_even_without_an_env_connection(tmp_path):
    store = ConnectionStore(tmp_path / "connections.json")

    with pytest.raises(ConfigError, match="^'기본 연결'은 정해진 이름이라 쓸 수 없습니다.$"):
        store.save(connection_from_form({**FORM, "name": ENV_NAME}))
    assert store.list() == []


def test_check_connection_reports_a_timeout():
    import httpx2
    from fake_llm import make_client

    from llm_lab.connections import check_connection

    def slow(request):
        raise httpx2.ReadTimeout("secret detail", request=request)

    def refused(request):
        raise httpx2.ConnectError("secret detail", request=request)

    base = Settings(base_url="", api_key="", model="")
    connection = connection_from_form(FORM)

    assert check_connection(connection, base, lambda s: make_client(slow)) == "연결 실패(시간 초과)"
    assert check_connection(connection, base, lambda s: make_client(refused)) == "연결 실패"


def test_env_connection_is_called_the_default_connection_and_old_name_still_finds_it(tmp_path):
    # 비개발자가 쓰는 화면이라 .env 대신 "기본 연결"로 부른다. 예전 작업에 적힌 ".env"도 찾는다
    settings = Settings(base_url="https://env.test/v1", api_key="secret-env", model="m")
    env = connection_from_settings(ENV_NAME, settings, read_only=True)
    store = ConnectionStore(tmp_path / "connections.json", env)

    assert ENV_NAME == "기본 연결"
    assert store.get(".env") is env
    with pytest.raises(ConfigError, match="정해진 이름"):
        store.save(connection_from_form({**FORM, "name": ".env"}))


def test_default_connection_can_be_renamed_and_old_names_still_find_it(tmp_path):
    settings = Settings(base_url="https://env.test/v1", api_key="secret-env", model="m")
    env = connection_from_settings(ENV_NAME, settings, read_only=True)
    path = tmp_path / "connections.json"
    store = ConnectionStore(path, env)
    store.save(connection_from_form({**FORM, "name": "A"}))

    store.rename_default("사내 게이트웨이")

    assert [c.name for c in store.list()] == ["사내 게이트웨이", "A"]
    assert store.current().name == "사내 게이트웨이"
    for old in (ENV_NAME, ".env", "사내 게이트웨이"):
        assert store.get(old).name == "사내 게이트웨이"
    again = ConnectionStore(path, env)  # 다시 켜도 이름이 남는다
    assert again.current().name == "사내 게이트웨이" and again.get(ENV_NAME) is not None
    store.rename_default("게이트웨이 2")
    assert store.get("사내 게이트웨이").name == "게이트웨이 2"  # 그 전 이름도 찾는다
    with pytest.raises(ConfigError, match="같은 이름"):
        store.rename_default("A")
    with pytest.raises(ConfigError, match="이름"):
        store.rename_default("  ")
    with pytest.raises(ConfigError, match="^기본 연결이 쓰고 있는 이름입니다"):
        store.save(connection_from_form({**FORM, "name": "게이트웨이 2"}))
    assert "secret-env" not in path.read_text(encoding="utf-8")


def test_an_old_default_name_can_be_used_for_a_new_connection(tmp_path):
    # 기본 연결의 예전 이름은 새 연결이 가져갈 수 있다. 그 뒤로 그 이름은 새 연결을 가리킨다
    settings = Settings(base_url="https://env.test/v1", api_key="secret-env", model="m")
    env = connection_from_settings(ENV_NAME, settings, read_only=True)
    path = tmp_path / "connections.json"
    store = ConnectionStore(path, env)
    store.rename_default("LLM")
    store.rename_default("사내 게이트웨이")

    store.save(connection_from_form({**FORM, "name": "LLM"}))

    assert store.get("LLM").read_only is False
    assert ConnectionStore(path, env).get("LLM").read_only is False  # 다시 켜도 같다
    assert store.get(ENV_NAME).name == "사내 게이트웨이"


def test_rename_default_without_env_connection_is_refused(tmp_path):
    store = ConnectionStore(tmp_path / "connections.json")

    with pytest.raises(ConfigError, match="기본 연결이 없습니다"):
        store.rename_default("새 이름")
