from pathlib import Path

import pytest

from llm_lab.config import ConfigError, Settings, load_settings, settings_from_env

VALID = {"LLM_BASE_URL": "https://llm.test/v1", "LLM_API_KEY": "secret-key", "LLM_MODEL": "m1"}


def test_settings_from_env_reads_required_values_and_default_timeout():
    settings = settings_from_env(VALID)

    assert settings == Settings(
        base_url="https://llm.test/v1", api_key="secret-key", model="m1", timeout=60.0
    )


def test_missing_required_values_are_all_named():
    with pytest.raises(ConfigError) as exc_info:
        settings_from_env({})

    message = str(exc_info.value)
    assert "LLM_BASE_URL" in message
    assert "LLM_MODEL" in message
    assert "LLM_API_KEY" not in message


def test_blank_value_counts_as_missing():
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        settings_from_env({**VALID, "LLM_MODEL": "   "})


def test_api_key_is_optional():
    # 인증 없이 열어 둔 사내 서버(vLLM 등)는 키가 없다
    settings = settings_from_env({"LLM_BASE_URL": "https://llm.test/v1", "LLM_MODEL": "m1"})

    assert settings.api_key == ""


@pytest.mark.parametrize("suffix", ["/chat/completions", "/chat/completions/", "/"])
def test_base_url_drops_endpoint_path(suffix):
    # requests로 직접 호출하던 전체 주소를 그대로 붙여 넣어도 SDK가 경로를 두 번 붙이지 않게
    settings = settings_from_env({**VALID, "LLM_BASE_URL": f"https://llm.test/v1{suffix}"})

    assert settings.base_url == "https://llm.test/v1"


def test_verify_ssl_and_thinking_defaults():
    settings = settings_from_env(VALID)

    assert settings.verify_ssl is True
    assert settings.enable_thinking is None


@pytest.mark.parametrize(
    ("raw", "expected"), [("false", False), ("FALSE", False), ("0", False), ("true", True)]
)
def test_verify_ssl_is_parsed_as_bool(raw, expected):
    assert settings_from_env({**VALID, "LLM_VERIFY_SSL": raw}).verify_ssl is expected


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("no", False), ("on", True)])
def test_enable_thinking_is_parsed_as_bool(raw, expected):
    assert settings_from_env({**VALID, "LLM_ENABLE_THINKING": raw}).enable_thinking is expected


@pytest.mark.parametrize("key", ["LLM_VERIFY_SSL", "LLM_ENABLE_THINKING"])
def test_invalid_bool_is_rejected(key):
    with pytest.raises(ConfigError, match=key):
        settings_from_env({**VALID, key: "nope"})


def test_timeout_is_parsed_as_float():
    assert settings_from_env({**VALID, "LLM_TIMEOUT": "15"}).timeout == 15.0


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "nan", "inf"])
def test_invalid_timeout_is_rejected(bad):
    with pytest.raises(ConfigError, match="LLM_TIMEOUT"):
        settings_from_env({**VALID, "LLM_TIMEOUT": bad})


def test_repr_hides_api_key():
    assert "secret-key" not in repr(settings_from_env(VALID))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """임시 디렉터리로 이동하고 LLM_* 환경변수를 비운다."""
    monkeypatch.chdir(tmp_path)
    for key in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT",
        "LLM_VERIFY_SSL",
        "LLM_ENABLE_THINKING",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_load_settings_reads_dotenv_file(isolated):
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=m1\n",
        encoding="utf-8",
    )

    assert load_settings().model == "m1"


def test_load_settings_accepts_utf8_bom(isolated):
    # 메모장·PowerShell 5.1은 UTF-8을 BOM과 함께 저장한다
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=m1\n",
        encoding="utf-8-sig",
    )

    assert load_settings().base_url == "https://llm.test/v1"


def test_os_environment_overrides_dotenv(isolated, monkeypatch):
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "from-env")

    assert load_settings().model == "from-env"


def test_empty_os_environment_value_does_not_hide_dotenv(isolated, monkeypatch):
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "")

    assert load_settings().model == "from-file"


def test_whitespace_os_environment_value_does_not_hide_dotenv(isolated, monkeypatch):
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "   ")

    assert load_settings().model == "from-file"


def test_utf16_dotenv_gets_encoding_hint(isolated):
    # PowerShell 5.1의 `>`·Out-File은 UTF-16으로 저장한다
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=m1\n",
        encoding="utf-16",
    )

    with pytest.raises(ConfigError, match="UTF-8"):
        load_settings()


def test_load_settings_without_dotenv_reports_missing(isolated):
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        load_settings()


def test_missing_values_without_dotenv_name_the_current_folder(isolated):
    with pytest.raises(ConfigError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert ".env가 없습니다" in message
    assert str(Path.cwd()) in message


def test_missing_values_with_dotenv_present_do_not_claim_it_is_absent(isolated):
    (isolated / ".env").write_text("LLM_MODEL=m1\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "LLM_BASE_URL" in message
    assert ".env가 없습니다" not in message


def test_invalid_timeout_without_dotenv_does_not_claim_dotenv_is_absent(isolated, monkeypatch):
    for key, value in {**VALID, "LLM_TIMEOUT": "abc"}.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "LLM_TIMEOUT" in message
    assert ".env가 없습니다" not in message


def test_load_settings_ignores_dotenv_in_parent_directory(isolated, monkeypatch):
    # 저장소 루트의 실제 .env가 하위 디렉터리 실행(특히 테스트)에 섞이지 않아야 한다
    (isolated / ".env").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_API_KEY=secret-key\nLLM_MODEL=m1\n",
        encoding="utf-8",
    )
    child = isolated / "sub"
    child.mkdir()
    monkeypatch.chdir(child)

    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        load_settings()
