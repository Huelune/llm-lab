"""회사 LLM 접속 설정 로딩."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED_VARS = ("LLM_BASE_URL", "LLM_MODEL")
OPTIONAL_VARS = (
    "LLM_API_KEY",
    "LLM_PROJECT_ID",
    "LLM_PROJECT_HEADER",
    "LLM_TIMEOUT",
    "LLM_VERIFY_SSL",
    "LLM_ENABLE_THINKING",
    "LLM_CLIENT",
    "LLM_API_VERSION",
    "LLM_WORKERS",
    "LLM_FOLLOW_UPS",
)
DEFAULT_PROJECT_HEADER = "OpenAI-Project"
# llm-fix-server가 LLM에 동시에 보내는 요청 수. 게이트웨이 한도는 llm-probe --parallel로 잰다
DEFAULT_WORKERS = 2
MAX_WORKERS = 16
# 묶음마다 LLM이 보이지 않는 정의를 되물을 수 있는 횟수. 0이면 되묻지 않는다(예전과 같은 요청)
DEFAULT_FOLLOW_UPS = 2
MAX_FOLLOW_UPS = 3
CLIENT_KINDS = ("openai", "azure")
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
    # 키와 별도로 프로젝트 ID 헤더를 요구하는 게이트웨이용. 비어 있으면 헤더를 보내지 않는다
    project_id: str = field(default="", repr=False)
    project_header: str = DEFAULT_PROJECT_HEADER
    # openai: LLM_BASE_URL/chat/completions에 Bearer 키.
    # azure: 회사 샘플처럼 AzureOpenAI로 LLM_BASE_URL/openai/deployments/<모델>/… 에 api-key 헤더
    client_kind: str = "openai"
    api_version: str = ""
    workers: int = DEFAULT_WORKERS
    follow_ups: int = DEFAULT_FOLLOW_UPS


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

    client_kind = values["LLM_CLIENT"].lower() or "openai"
    if client_kind not in CLIENT_KINDS:
        raise ConfigError(f"LLM_CLIENT은 openai 또는 azure여야 합니다: {values['LLM_CLIENT']!r}")
    if client_kind == "azure" and not values["LLM_API_KEY"]:
        raise ConfigError("LLM_CLIENT=azure면 LLM_API_KEY가 필요합니다 (api-key 헤더로 보냄)")

    workers = DEFAULT_WORKERS
    if values["LLM_WORKERS"]:
        text = values["LLM_WORKERS"]
        if not text.isdigit() or not 1 <= int(text) <= MAX_WORKERS:
            raise ConfigError(f"LLM_WORKERS는 1~{MAX_WORKERS}의 정수여야 합니다: {text!r}")
        workers = int(text)

    follow_ups = DEFAULT_FOLLOW_UPS
    if values["LLM_FOLLOW_UPS"]:
        text = values["LLM_FOLLOW_UPS"]
        if not text.isdigit() or not 0 <= int(text) <= MAX_FOLLOW_UPS:
            raise ConfigError(f"LLM_FOLLOW_UPS는 0~{MAX_FOLLOW_UPS}의 정수여야 합니다: {text!r}")
        follow_ups = int(text)

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
        project_id=values["LLM_PROJECT_ID"],
        project_header=values["LLM_PROJECT_HEADER"] or DEFAULT_PROJECT_HEADER,
        client_kind=client_kind,
        api_version=values["LLM_API_VERSION"],
        workers=workers,
        follow_ups=follow_ups,
    )


def load_settings(profile: str | None = None) -> Settings:
    """현재 디렉터리의 .env를 읽고, 비어 있지 않은 OS 환경변수를 우선 적용한다.

    profile을 주면 .env 대신 .env.<profile>을 읽는다 (같은 폴더에서 설정을 둘 쓸 때).
    """
    path = Path(f".env.{profile}") if profile else Path(".env")
    if profile and not path.is_file():
        raise ConfigError(f"프로필 설정 파일이 없습니다: {path} (.env.example을 복사해 만드세요)")
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
