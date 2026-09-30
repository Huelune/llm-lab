"""회사 LLM 접속 설정 로딩."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import dotenv_values, find_dotenv

REQUIRED_VARS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
DEFAULT_TIMEOUT = 60.0


class ConfigError(Exception):
    """필수 설정이 없거나 값이 잘못됨."""


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = DEFAULT_TIMEOUT


def settings_from_env(env: Mapping[str, str | None]) -> Settings:
    """환경변수 매핑에서 Settings를 만든다. 빈 문자열은 없는 값으로 본다."""
    values = {key: (env.get(key) or "").strip() for key in (*REQUIRED_VARS, "LLM_TIMEOUT")}
    missing = [key for key in REQUIRED_VARS if not values[key]]
    if missing:
        raise ConfigError(f"필수 설정이 없습니다: {', '.join(missing)} (.env.example 참고)")

    timeout = DEFAULT_TIMEOUT
    if values["LLM_TIMEOUT"]:
        try:
            timeout = float(values["LLM_TIMEOUT"])
        except ValueError:
            raise ConfigError(
                f"LLM_TIMEOUT은 초 단위 숫자여야 합니다: {values['LLM_TIMEOUT']!r}"
            ) from None
        if timeout <= 0:
            raise ConfigError(f"LLM_TIMEOUT은 0보다 커야 합니다: {values['LLM_TIMEOUT']!r}")

    return Settings(
        base_url=values["LLM_BASE_URL"],
        api_key=values["LLM_API_KEY"],
        model=values["LLM_MODEL"],
        timeout=timeout,
    )


def load_settings() -> Settings:
    """현재 디렉터리부터 위로 .env를 찾아 읽고, 비어 있지 않은 OS 환경변수를 우선 적용한다."""
    path = find_dotenv(usecwd=True)
    merged: dict[str, str | None] = dict(dotenv_values(path)) if path else {}
    merged.update({key: value for key, value in os.environ.items() if value})
    return settings_from_env(merged)
