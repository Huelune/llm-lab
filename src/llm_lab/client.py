"""회사 LLM용 OpenAI 클라이언트 생성."""

from __future__ import annotations

from openai import OpenAI, omit

from llm_lab.config import Settings, load_settings


def get_client(settings: Settings | None = None) -> OpenAI:
    """회사 LLM 주소·키·타임아웃이 설정된 OpenAI 클라이언트를 반환한다."""
    settings = settings or load_settings()
    # SDK는 OPENAI_ORG_ID·OPENAI_PROJECT_ID·OPENAI_CUSTOM_HEADERS도 읽는다. 커스텀 헤더는 인증
    # 헤더 뒤에 합쳐지므로 개인 키가 회사 키를 덮어쓸 수 있어, 인증은 명시하고 조직·프로젝트
    # 헤더는 omit으로 제거한다.
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
        default_headers={
            "Authorization": f"Bearer {settings.api_key}",
            "OpenAI-Organization": omit,  # type: ignore[dict-item]
            "OpenAI-Project": omit,  # type: ignore[dict-item]
        },
    )
