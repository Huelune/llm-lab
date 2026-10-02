"""회사 LLM용 OpenAI 클라이언트 생성."""

from __future__ import annotations

from typing import Any

from openai import DefaultHttpxClient, OpenAI, omit
from openai._models import FinalRequestOptions

from llm_lab.config import Settings, load_settings

# SDK는 키가 비어 있으면 생성을 거부하므로 자리만 채운다. 이 값은 요청에 실리지 않는다.
NO_KEY_PLACEHOLDER = "no-key"


class CompanyOpenAI(OpenAI):
    """키 없는 서버면 인증 헤더를 빼고, chat/completions 본문에 서버 전용 필드를 붙인다."""

    omit_authorization: bool = False
    chat_extra_body: dict[str, Any] = {}

    def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions:
        options = super()._prepare_options(options)
        if self.omit_authorization:
            # SDK는 요청별 헤더에서 명시적으로 omit된 경우에만 인증 헤더 없는 요청을 허용한다
            options.headers = {**(options.headers or {}), "Authorization": omit}
        if self.chat_extra_body and options.url == "/chat/completions":
            # 호출마다 넘긴 extra_body가 기본값보다 우선한다
            options.extra_json = {**self.chat_extra_body, **(options.extra_json or {})}
        return options

    def copy(self, **kwargs: Any) -> CompanyOpenAI:
        copied = super().copy(**kwargs)
        copied.omit_authorization = self.omit_authorization
        copied.chat_extra_body = self.chat_extra_body
        return copied

    with_options = copy


def get_client(settings: Settings | None = None) -> OpenAI:
    """회사 LLM 주소·키·타임아웃·TLS·추가 본문이 설정된 OpenAI 클라이언트를 반환한다."""
    settings = settings or load_settings()
    # SDK는 OPENAI_ORG_ID·OPENAI_PROJECT_ID·OPENAI_CUSTOM_HEADERS도 읽는다. 커스텀 헤더는 인증
    # 헤더 뒤에 합쳐지므로 개인 키가 회사 키를 덮어쓸 수 있어, 인증은 명시하고 조직·프로젝트
    # 헤더는 omit으로 제거한다. 키가 없으면 인증 헤더 자체를 omit한다.
    authorization = f"Bearer {settings.api_key}" if settings.api_key else omit
    client = CompanyOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key or NO_KEY_PLACEHOLDER,
        timeout=settings.timeout,
        http_client=None if settings.verify_ssl else DefaultHttpxClient(verify=False),
        default_headers={
            "Authorization": authorization,  # type: ignore[dict-item]
            "OpenAI-Organization": omit,  # type: ignore[dict-item]
            "OpenAI-Project": omit,  # type: ignore[dict-item]
        },
    )
    client.omit_authorization = not settings.api_key
    if settings.enable_thinking is not None:
        client.chat_extra_body = {
            "chat_template_kwargs": {"enable_thinking": settings.enable_thinking}
        }
    return client
