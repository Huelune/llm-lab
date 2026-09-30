"""회사 LLM 연결·기능 점검 CLI."""

from __future__ import annotations

import ssl

import openai

SSL_ERROR = "SSL"
NETWORK_ERROR = "네트워크"
AUTH_ERROR = "인증"
PATH_ERROR = "경로"
UNSUPPORTED_ERROR = "기능 미지원"
SERVER_ERROR = "서버"
FATAL_CATEGORIES = {SSL_ERROR, NETWORK_ERROR, AUTH_ERROR}


def _has_ssl_cause(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _server_message(exc: openai.APIStatusError) -> str:
    if isinstance(exc.body, dict) and exc.body.get("message"):
        return str(exc.body["message"])
    return exc.message


def classify_error(exc: openai.APIError) -> tuple[str, str]:
    """SDK 예외를 (분류, 안내 문구)로 바꾼다."""
    if isinstance(exc, openai.APITimeoutError):
        return NETWORK_ERROR, "응답 시간 초과 - 사내망/VPN 연결과 LLM_TIMEOUT 값을 확인하세요"
    if isinstance(exc, openai.APIConnectionError):
        if _has_ssl_cause(exc):
            return SSL_ERROR, (
                "인증서 검증 실패 - Windows 인증서 저장소에 사내 루트 인증서가 있는지 확인하세요"
            )
        return NETWORK_ERROR, "서버에 접속할 수 없음 - 사내망/VPN 연결과 LLM_BASE_URL을 확인하세요"
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return AUTH_ERROR, f"인증 실패({exc.status_code}) - LLM_API_KEY를 확인하세요"
    if isinstance(exc, openai.NotFoundError):
        return PATH_ERROR, (
            "경로를 찾을 수 없음(404) - LLM_BASE_URL 끝에 /v1이 있는지, "
            "LLM_MODEL이 맞는지 확인하세요"
        )
    if isinstance(exc, openai.BadRequestError):
        return UNSUPPORTED_ERROR, (
            f"요청 거부(400) - 지원하지 않는 기능일 수 있음: {_server_message(exc)}"
        )
    if isinstance(exc, openai.APIStatusError):
        return SERVER_ERROR, f"서버 오류({exc.status_code}): {_server_message(exc)}"
    return SERVER_ERROR, str(exc)
