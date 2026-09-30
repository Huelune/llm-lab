"""회사 LLM용 OpenAI 클라이언트 생성."""

from __future__ import annotations

from openai import OpenAI

from llm_lab.config import Settings, load_settings


def get_client(settings: Settings | None = None) -> OpenAI:
    """회사 LLM 주소·키·타임아웃이 설정된 OpenAI 클라이언트를 반환한다."""
    settings = settings or load_settings()
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=settings.timeout)
