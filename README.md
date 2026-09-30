# llm-lab

회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구와 공용 클라이언트입니다.

## 준비

1. 사내망(또는 VPN)에 연결합니다.
2. 의존성을 설치합니다.

   ```bash
   uv sync
   ```

3. 프로젝트 루트에서 `.env.example`을 `.env`로 복사하고 값을 채웁니다. `.env`는 git에 커밋되지 않습니다.

   | 변수 | 필수 | 설명 |
   |---|---|---|
   | `LLM_BASE_URL` | ✅ | 예: `https://llm.사내도메인/v1` (`/v1`까지 포함) |
   | `LLM_API_KEY` | ✅ | 발급받은 키 |
   | `LLM_MODEL` | ✅ | 사용할 모델명 |
   | `LLM_TIMEOUT` | – | 요청 타임아웃(초), 기본 60 |

   OS 환경변수에 같은 이름이 있으면 `.env`보다 우선합니다. 키·조직·프로젝트는 항상 `LLM_*` 설정에서만 가져오며, `OPENAI_API_KEY`·`OPENAI_BASE_URL`·`OPENAI_ORG_ID`·`OPENAI_PROJECT_ID`는 무시하고 `OPENAI_CUSTOM_HEADERS`의 `Authorization`도 무시합니다. 그 밖의 `OPENAI_CUSTOM_HEADERS` 항목은 그대로 요청에 붙습니다.

   `HTTP_PROXY`·`HTTPS_PROXY`·`NO_PROXY` 환경변수는 그대로 적용됩니다(Windows는 시스템 프록시 설정도 반영). 사내 LLM 호스트에 프록시 없이 직접 붙어야 하면 그 호스트를 `NO_PROXY`에 추가하고, 프록시를 거쳐야 하면 `HTTPS_PROXY`를 설정하세요.

## 점검 실행

```bash
uv run llm-probe
```

명령은 프로젝트 루트(`.env`가 있는 폴더)에서 실행합니다. `.env`는 현재 폴더에서만 읽습니다.

응답 원문 JSON까지 보려면 `--verbose`를 붙입니다. API 키는 어떤 출력에도 나오지 않습니다.

| 항목 | 확인 내용 |
|---|---|
| 1. 연결·모델 목록 | `/models` 호출, `LLM_MODEL`이 목록에 있는지 |
| 2. 기본 채팅 | 짧은 질문에 응답하는지, 토큰 사용량 |
| 3. 스트리밍 | `stream=True` 응답, 첫 토큰까지 시간 |
| 4. Tool calling | 도구(`report_issue`)를 올바른 인자로 호출하는지 |
| 5. JSON 출력 | `json_schema` 지원 여부, 안 되면 `json_object` |

- 상태: `✅ 지원` / `⚠️ 부분` / `❌ 실패` / `⏭ 건너뜀`
- 네트워크·SSL·인증 오류가 나면 그 자리에서 중단하고 원인과 확인할 것을 보여줍니다.
- 종료 코드: 기본 채팅이 성공하면 0, 아니면 1

## 코드에서 사용하기

```python
from llm_lab.client import get_client
from llm_lab.config import load_settings

settings = load_settings()
client = get_client(settings)
response = client.chat.completions.create(
    model=settings.model,
    messages=[{"role": "user", "content": "안녕하세요"}],
)
print(response.choices[0].message.content)
```

`uv run python`으로 REPL을 열어 바로 실험할 수 있습니다.

## 개발

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
```

테스트는 가짜 HTTP 서버를 쓰므로 사내망·키 없이 실행됩니다.
