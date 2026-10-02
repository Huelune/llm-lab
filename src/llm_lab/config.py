"""회사 LLM 접속 설정 로딩."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED_VARS = ("LLM_BASE_URL", "LLM_MODEL")
OPTIONAL_VARS = ("LLM_API_KEY", "LLM_TIMEOUT", "LLM_VERIFY_SSL", "LLM_ENABLE_THINKING")
DEFAULT_TIMEOUT = 60.0
BOOL_VALUES = {
    **dict.fromkeys(("true", "1", "yes", "on"), True),
    **dict.fromkeys(("false", "0", "no", "off"), False),
}


class ConfigError(Exception):
    """필수 설정이 없거나 값이 잘못됨."""


class MissingSettingsError(ConfigError):
    """필수 설정 값이 비어 있음 (값이 잘못된 경우와 구분해 .env 안내를 붙이기 위함)."""


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = DEFAULT_TIMEOUT
    verify_ssl: bool = True
    # None이면 chat_template_kwargs를 보내지 않아 서버 기본값을 따른다
    enable_thinking: bool | None = None


def _parse_bool(key: str, value: str) -> bool:
    try:
        return BOOL_VALUES[value.lower()]
    except KeyError:
        raise ConfigError(f"{key}은 true 또는 false여야 합니다: {value!r}") from None


def settings_from_env(env: Mapping[str, str | None]) -> Settings:
    """환경변수 매핑에서 Settings를 만든다. 빈 문자열은 없는 값으로 본다."""
    values = {key: (env.get(key) or "").strip() for key in (*REQUIRED_VARS, *OPTIONAL_VARS)}
    missing = [key for key in REQUIRED_VARS if not values[key]]
    if missing:
        raise MissingSettingsError(
            f"필수 설정이 없습니다: {', '.join(missing)} (.env.example 참고)"
        )

    timeout = DEFAULT_TIMEOUT
    if values["LLM_TIMEOUT"]:
        try:
            timeout = float(values["LLM_TIMEOUT"])
        except ValueError:
            raise ConfigError(
                f"LLM_TIMEOUT은 초 단위 숫자여야 합니다: {values['LLM_TIMEOUT']!r}"
            ) from None
        if not math.isfinite(timeout):
            raise ConfigError(f"LLM_TIMEOUT은 유한한 숫자여야 합니다: {values['LLM_TIMEOUT']!r}")
        if timeout <= 0:
            raise ConfigError(f"LLM_TIMEOUT은 0보다 커야 합니다: {values['LLM_TIMEOUT']!r}")

    # SDK가 /chat/completions를 직접 붙이므로, requests용 전체 주소를 넣었으면 잘라낸다
    base_url = values["LLM_BASE_URL"].rstrip("/").removesuffix("/chat/completions")
    thinking = values["LLM_ENABLE_THINKING"]
    return Settings(
        base_url=base_url,
        api_key=values["LLM_API_KEY"],
        model=values["LLM_MODEL"],
        timeout=timeout,
        verify_ssl=_parse_bool("LLM_VERIFY_SSL", values["LLM_VERIFY_SSL"] or "true"),
        enable_thinking=_parse_bool("LLM_ENABLE_THINKING", thinking) if thinking else None,
    )


def load_settings() -> Settings:
    """현재 디렉터리의 .env를 읽고, 비어 있지 않은 OS 환경변수를 우선 적용한다."""
    path = Path(".env")
    merged: dict[str, str | None] = {}
    if path.is_file():
        try:
            merged.update(dotenv_values(path))
        except UnicodeDecodeError:
            raise ConfigError(".env를 UTF-8로 저장하세요 (현재 인코딩을 읽을 수 없음)") from None
    merged.update({key: value for key, value in os.environ.items() if value and value.strip()})
    try:
        return settings_from_env(merged)
    except MissingSettingsError as exc:
        if path.is_file():
            raise
        raise ConfigError(f"{exc} - 현재 폴더({Path.cwd()})에 .env가 없습니다") from exc
