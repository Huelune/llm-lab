"""회사 LLM용 OpenAI 클라이언트 생성."""

from __future__ import annotations

from typing import Any

from openai import AzureOpenAI, DefaultHttpxClient, OpenAI, omit
from openai._models import FinalRequestOptions

from llm_lab.config import Settings, load_settings

# SDK는 키가 비어 있으면 생성을 거부하므로 자리만 채운다. 이 값은 요청에 실리지 않는다.
NO_KEY_PLACEHOLDER = "no-key"


class _CompanyClient:
    """키 없는 서버면 인증 헤더를 빼고, chat/completions 본문에 서버 전용 필드를 붙인다."""

    omit_authorization: bool = False
    chat_extra_body: dict[str, Any] = {}

    def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions:
        options = super()._prepare_options(options)  # type: ignore[misc]
        if self.omit_authorization:
            # SDK는 요청별 헤더에서 명시적으로 omit된 경우에만 인증 헤더 없는 요청을 허용한다
            options.headers = {**(options.headers or {}), "Authorization": omit}
        if self.chat_extra_body and options.url == "/chat/completions":
            # 호출마다 넘긴 extra_body가 기본값보다 우선한다.
            # Azure의 deployments 경로는 이 뒤 _build_request에서 붙는다
            options.extra_json = {**self.chat_extra_body, **(options.extra_json or {})}
        return options

    def copy(self, **kwargs: Any) -> _CompanyClient:
        copied = super().copy(**kwargs)  # type: ignore[misc]
        copied.omit_authorization = self.omit_authorization
        copied.chat_extra_body = self.chat_extra_body
        return copied

    with_options = copy


class CompanyOpenAI(_CompanyClient, OpenAI):
    """Bearer 키로 LLM_BASE_URL/chat/completions를 부른다."""


class CompanyAzureOpenAI(_CompanyClient, AzureOpenAI):
    """회사 샘플처럼 api-key 헤더로 LLM_BASE_URL/openai/deployments/<모델>/… 를 부른다."""


def get_client(settings: Settings | None = None) -> OpenAI:
    """회사 LLM 주소·키·타임아웃·TLS·추가 본문이 설정된 OpenAI 클라이언트를 반환한다."""
    settings = settings or load_settings()
    azure = settings.client_kind == "azure"
    # SDK는 OPENAI_ORG_ID·OPENAI_PROJECT_ID·OPENAI_CUSTOM_HEADERS도 읽는다. 커스텀 헤더는 인증
    # 헤더 뒤에 합쳐지므로 개인 키가 회사 키를 덮어쓸 수 있어, 인증은 명시하고 조직·프로젝트
    # 헤더는 omit으로 제거한다. 키가 없거나 Azure 방식(api-key 헤더)이면 Authorization을 omit한다.
    authorization = f"Bearer {settings.api_key}" if settings.api_key and not azure else omit
    headers: dict[str, Any] = {
        "Authorization": authorization,
        "OpenAI-Organization": omit,
        "OpenAI-Project": omit,
    }
    if settings.project_id:
        # 회사 프로젝트 ID는 게이트웨이가 정한 이름의 헤더로 보낸다 (기본 이름이면 위 omit을 덮어씀)
        headers[settings.project_header] = settings.project_id
    common: dict[str, Any] = {
        "api_key": settings.api_key or NO_KEY_PLACEHOLDER,
        "timeout": settings.timeout,
        "http_client": None if settings.verify_ssl else DefaultHttpxClient(verify=False),
        "default_headers": headers,
    }
    client: _CompanyClient
    if azure:
        # api_key를 직접 넘기므로 SDK는 AZURE_OPENAI_* 환경변수를 읽지 않는다
        client = CompanyAzureOpenAI(
            azure_endpoint=settings.base_url, api_version=settings.api_version, **common
        )
    else:
        client = CompanyOpenAI(base_url=settings.base_url, **common)
    client.omit_authorization = not settings.api_key
    if settings.enable_thinking is not None:
        client.chat_extra_body = {
            "chat_template_kwargs": {"enable_thinking": settings.enable_thinking}
        }
    return client  # type: ignore[return-value]


def chat_endpoint(client: OpenAI, model: str) -> str:
    """기록에 적는 chat/completions 주소. Azure 방식은 SDK가 deployments 경로를 끼워 넣는다."""
    if isinstance(client, AzureOpenAI):
        return f"{client.base_url}deployments/{model}/chat/completions"
    return f"{client.base_url}chat/completions"
