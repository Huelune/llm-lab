# 회사 LLM 연결·기능 점검 도구 (llm-probe) 설계

- 작성일: 2026-09-30
- 상태: 검토 대기

## 1. 배경과 목표

회사 LLM(OpenAI 호환 API, 사내망 전용)으로 **코드 정적 분석 + 자동 수정 로컬 CLI**를 만들 예정이다.
이 문서는 그 전 단계인 **1단계: 연결과 사용법 탐색**의 기반을 다룬다.

### 범위

- 포함: 설정 로딩, 공용 클라이언트 생성, 연결·기능 점검 CLI(`llm-probe`), 테스트
- 제외: 정적 분석·수정 기능(2단계, 별도 spec), 에이전트 프레임워크(LangChain 등), 다른 LLM 공급자 지원

### 성공 기준

1. `.env`에 URL·키·모델명만 넣고 `uv run llm-probe` 한 번으로 연결 여부와 기능 지원 매트릭스를 확인할 수 있다.
2. 실패 시 원인(네트워크 / SSL / 인증 / URL·모델명 / 기능 미지원)이 구분되어 표시된다.
3. 2단계에 필요한 **구조화된 출력**(tool calling, JSON 출력) 지원 여부가 확인된다.
4. 2단계 코드는 `Settings`와 `get_client()`만 import해서 재사용한다.

## 2. 기술 선택

| 항목 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.14 | 로컬에 설치됨 |
| 패키지 관리 | uv | 로컬에 설치됨, `uv run`으로 실행 일원화 |
| LLM 클라이언트 | 공식 `openai` SDK (`base_url` 지정) | 스트리밍·tool calling·타임아웃·재시도 내장. 호환성 문제는 점검에서 드러나며, 필요 시 해당 부분만 `httpx` 직접 호출로 대체 |
| 설정 | `python-dotenv` | `.env` 로딩 |
| 개발 도구 | `pytest`, `ruff` | 테스트, 린트·포맷 |

검토 후 제외한 대안: LiteLLM(단일 엔드포인트엔 과함), `httpx` 직접 구현(스트리밍·tool calling 파싱을 직접 작성해야 함).

## 3. 프로젝트 구조

```
D:\codes\llm\
├─ pyproject.toml      # 의존성: openai, python-dotenv / dev: pytest, ruff
│                      # 스크립트: llm-probe = "llm_lab.probe:main"
├─ .env.example        # 설정 템플릿 (커밋)
├─ .env                # 실제 값 (gitignore)
├─ .gitignore
├─ README.md           # 설정·실행 방법, REPL에서 get_client() 쓰는 예시
├─ src/llm_lab/
│  ├─ __init__.py
│  ├─ config.py        # Settings, load_settings()
│  ├─ client.py        # get_client()
│  └─ probe.py         # 점검 항목, 오류 분류, CLI 진입점 main()
└─ tests/
   ├─ test_config.py
   └─ test_probe.py
```

## 4. 설정 (`config.py`)

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `LLM_BASE_URL` | ✅ | – | 예: `https://llm.사내도메인/v1` |
| `LLM_API_KEY` | ✅ | – | 발급받은 키 |
| `LLM_MODEL` | ✅ | – | 사용할 모델명 |
| `LLM_TIMEOUT` | – | `60` | 요청 타임아웃(초) |

- `Settings`는 불변 dataclass(`base_url`, `api_key`, `model`, `timeout`).
- `load_settings()`는 `.env`를 로드(이미 설정된 환경변수가 우선)한 뒤 검증한다.
  - 필수값이 없으면 `ConfigError`를 발생시키고, 메시지에 **빠진 변수 이름을 모두** 나열한다.
  - `LLM_TIMEOUT`이 숫자가 아니면 `ConfigError`.
- `OPENAI_*`가 아닌 `LLM_*` 접두사를 쓰는 이유: SDK가 `OPENAI_API_KEY` 등을 자동으로 읽으므로 개인 OpenAI 키와 섞이는 것을 막는다. `get_client()`는 값을 항상 명시적으로 전달한다.

## 5. 클라이언트 (`client.py`)

```python
def get_client(settings: Settings | None = None) -> OpenAI
```

- `settings`가 없으면 `load_settings()`를 호출한다.
- `OpenAI(base_url=..., api_key=..., timeout=...)`를 반환한다. 재시도는 SDK 기본값을 따른다.
- 2단계의 공용 진입점은 이 함수와 `Settings`뿐이다.

## 6. 점검 CLI (`probe.py`)

### 6.1 실행

```
uv run llm-probe [--verbose]
```

- 점검용 클라이언트는 `get_client().with_options(max_retries=0)`를 쓴다. 실패 원인을 재시도 없이 즉시 확인하기 위해서다.
- 각 점검 함수는 `(client, model)`을 인자로 받아 `CheckResult`를 반환한다. 이렇게 하면 테스트에서 가짜 HTTP 서버를 주입할 수 있다.
- `CheckResult`: `name`, `status`(`ok` / `partial` / `fail` / `skip`), `detail`(한 줄 요약), `elapsed`(초, 선택), `raw`(`--verbose`용 응답 원문, 선택).

### 6.2 점검 항목

| # | 항목 | 방법 | 판정 |
|---|---|---|---|
| 1 | 연결·모델 목록 | `client.models.list()` | ✅ 목록에 `LLM_MODEL` 있음 / ⚠️ 목록은 오지만 모델 없음(모델명 확인 안내) / ⏭ 404(엔드포인트 미구현) |
| 2 | 기본 채팅 | 한 단어 답을 요구하는 짧은 프롬프트. `max_tokens`는 지정하지 않음(서버마다 `max_tokens`/`max_completion_tokens` 지원이 다르고, 추론형 모델은 작은 한도에서 빈 응답을 낼 수 있음) | ✅ 응답 텍스트 존재. 소요 시간·`usage` 토큰 수 기록(`usage`가 없으면 "토큰 정보 없음") |
| 3 | 스트리밍 | `stream=True` | ✅ 청크 수신. 첫 콘텐츠 청크까지 시간, 청크 수 기록 |
| 4 | Tool calling | 버그 있는 짧은 코드 + `report_issue(file, line, severity, message)` 도구, `tool_choice="auto"` | ✅ `tool_calls`에 `report_issue`가 있고 인자가 JSON으로 파싱되며 필수 필드가 있음 / ⚠️ 호출은 있으나 인자 파싱 실패·필드 누락 / ❌ 호출 없음 또는 400 |
| 5 | JSON 출력 | `response_format={"type": "json_schema", ...}`로 시도하고, 실패하면 `{"type": "json_object"}` 재시도 | ✅ json_schema 성공 + 스키마 필드 존재 / ⚠️ json_object로만 성공 / ❌ 둘 다 실패 |

### 6.3 출력 형식

```
회사 LLM 점검 — https://llm.사내/v1 (model: xxx)

[1] 연결·모델 목록    ✅  모델 3개, 'xxx' 확인됨          0.21s
[2] 기본 채팅         ✅  토큰 in 12 / out 3               0.84s
[3] 스트리밍          ✅  첫 토큰 0.31s, 청크 18개
[4] Tool calling      ✅  report_issue(line=3) 반환
[5] JSON 출력         ⚠️  json_schema 미지원 → json_object 성공

요약: 지원 4 / 부분 지원 1 / 미지원 0
```

- 헤더에는 `base_url`과 `model`만 출력한다. **API 키는 어떤 출력에도 포함하지 않는다**(`--verbose` 포함).
- `--verbose`: 각 항목 아래에 응답 원문 JSON(`model_dump()`)을 출력한다.

### 6.4 오류 분류

`classify_error(exc) -> (category, hint)`로 SDK 예외를 분류한다.

| 예외 | 분류 | 안내 |
|---|---|---|
| `APIConnectionError` (원인 체인에 `ssl.SSLError`) | SSL | 인증서 검증 실패 — 사내 인증서 설정 확인 |
| `APITimeoutError` | 네트워크 | 타임아웃 — 사내망/VPN 연결, `LLM_TIMEOUT` 확인 |
| `APIConnectionError` (그 외) | 네트워크 | 접속 불가 — 사내망/VPN 연결, `LLM_BASE_URL` 확인 |
| `AuthenticationError`(401), `PermissionDeniedError`(403) | 인증 | `LLM_API_KEY` 확인 |
| `NotFoundError`(404) | 경로 | URL에 `/v1` 포함 여부, `LLM_MODEL` 확인 |
| `BadRequestError`(400) | 기능 미지원 | 서버 메시지 원문을 함께 표시 |
| 그 외 `APIStatusError` | 서버 | 상태 코드와 서버 메시지 원문 |

### 6.5 흐름 제어와 종료 코드

- **중단 조건**: 어느 항목에서든 분류가 네트워크 / SSL / 인증이면 남은 항목을 실행하지 않고 원인과 안내를 출력한다.
- 그 외 실패(기능 미지원, 서버 오류)는 해당 항목만 ❌로 표시하고 다음 항목을 계속 실행한다.
- 설정 오류(`ConfigError`)는 점검 시작 전에 메시지를 출력하고 종료한다.
- 종료 코드: **2번(기본 채팅)이 ✅이면 0, 아니면 1.** 설정 오류도 1.

## 7. 테스트

`uv run pytest`는 네트워크와 실제 키 없이 실행된다.

| 대상 | 방식 | 케이스 |
|---|---|---|
| `config.py` | `monkeypatch`로 환경변수 설정/삭제, 임시 디렉터리에서 실행해 실제 `.env` 영향 차단 | 필수값 누락 시 빠진 변수 이름 전부 포함, `LLM_TIMEOUT` 기본값 60, 숫자가 아닌 timeout 거부 |
| 점검 항목 | `httpx.MockTransport` 가짜 서버를 `OpenAI(http_client=httpx.Client(transport=...))`에 주입 | 모델 목록 200(모델 있음/없음)·404, 채팅 성공, SSE 스트리밍, tool_calls 있음/없음/인자 깨짐, json_schema 400 → json_object 200, 연결 오류 시 중단 |
| `classify_error` | 실제 SDK 예외 객체 생성 | 401, 403, 404, 400, 타임아웃, SSL 원인 체인, 일반 연결 오류 |
| `main()` | 점검 결과를 주입해 종료 코드 확인 | 채팅 ✅ → 0, 채팅 ❌ → 1, 출력에 API 키 문자열이 없음 |

- 실제 회사 LLM 대상 검증은 `llm-probe` 실행 자체가 담당한다. 별도의 pytest 통합 테스트는 두지 않는다.
- 품질 도구: `ruff check`, `ruff format --check`.

## 8. 완료 확인 절차

1. `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`가 통과한다.
2. 사용자가 `.env`에 실제 값을 직접 입력한다(키가 채팅 기록에 남지 않도록).
3. 사내망에서 `uv run llm-probe`를 실행해 결과를 확인하고, 그 결과를 2단계 설계의 입력으로 쓴다.

## 9. 2단계로 넘길 것

- 공용 API: `llm_lab.config.Settings`, `llm_lab.config.load_settings`, `llm_lab.client.get_client`
- 점검 결과(특히 4·5번)에 따라 2단계에서 구조화된 출력을 tool calling으로 받을지, JSON 출력으로 받을지, 텍스트 파싱으로 받을지 결정한다.
