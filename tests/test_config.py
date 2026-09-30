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
        settings_from_env({"LLM_MODEL": "m1"})

    message = str(exc_info.value)
    assert "LLM_BASE_URL" in message
    assert "LLM_API_KEY" in message
    assert "LLM_MODEL" not in message


def test_blank_value_counts_as_missing():
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        settings_from_env({**VALID, "LLM_API_KEY": "   "})


def test_timeout_is_parsed_as_float():
    assert settings_from_env({**VALID, "LLM_TIMEOUT": "15"}).timeout == 15.0


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_invalid_timeout_is_rejected(bad):
    with pytest.raises(ConfigError, match="LLM_TIMEOUT"):
        settings_from_env({**VALID, "LLM_TIMEOUT": bad})


def test_repr_hides_api_key():
    assert "secret-key" not in repr(settings_from_env(VALID))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """임시 디렉터리로 이동하고 LLM_* 환경변수를 비운다."""
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT"):
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


def test_load_settings_without_dotenv_reports_missing(isolated):
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        load_settings()
