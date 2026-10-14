# 초기 시작 구성

회사 PC에서 처음 한 번 하는 일입니다. 코드를 받아 `.env`를 채우고, `llm-probe`로 회사 LLM이 이 도구에 맞는지 보고, 쓸 모델을 고릅니다.
결과를 전달할 때는 숫자와 표준 용어만 적습니다. 키·주소·모델명이 보이는 화면은 캡처하지 않습니다.

## 1. 초기 구성

### 코드와 도구

- [ ] github.com에 접속되면 clone합니다. 공개 저장소라 인증 없이 받을 수 있으니 PC에 GitHub 토큰을 남기지 않습니다.

  ```bash
  git clone https://github.com/Huelune/llm-lab.git
  ```

  접속되지 않으면 개발 PC에서 ZIP을 만들어 복사합니다.

  ```bash
  git archive -o llm-lab.zip HEAD
  ```

- [ ] `uv`를 설치합니다. Python은 `uv python install 3.14`로 받습니다.
- [ ] PyPI에 접속되지 않으면 사내 미러 주소를 `UV_DEFAULT_INDEX`에 넣습니다.
- [ ] 의존성을 설치합니다.

  ```bash
  uv sync
  ```

### `.env` 작성

`.env.example`을 `.env`로 복사하고 채웁니다. `.env`는 커밋되지 않습니다. 접속 방식에 따라 채우는 칸이 다릅니다.

| 칸 | `openai` 방식 (OpenAI 호환 `/v1` 서버) | `azure` 방식 (회사 샘플 코드가 `AzureOpenAI`를 쓸 때) |
|---|---|---|
| `LLM_CLIENT` | 비움 | `azure` |
| `LLM_BASE_URL` | `https://…/v1` | 샘플의 base_url 값 그대로. `/openai/…`는 SDK가 붙입니다 |
| `LLM_API_KEY` | 키. 키 없는 서버면 비움 | 키(필수). `api-key` 헤더로 나갑니다 |
| `LLM_PROJECT_ID`, `LLM_PROJECT_HEADER` | 프로젝트 ID를 받았을 때만 | 프로젝트 ID와 샘플에 적힌 헤더 이름 |
| `LLM_MODEL` | 모델명 | 배포(deployment) 이름 |

- [ ] 선택 항목(`LLM_TIMEOUT`, `LLM_VERIFY_SSL`, `LLM_ENABLE_THINKING`, `LLM_API_VERSION`, `LLM_WORKERS`, `LLM_FOLLOW_UPS`)은 `.env.example`의 설명을 보고 필요할 때만 채웁니다.
- [ ] 키와 프로젝트 ID는 채팅이나 스크린샷에 올리지 않습니다.

## 2. 되는지 파악하기

### 점검 실행

결과를 파일로 남깁니다. 화면과 같은 내용이 UTF-8로 저장되고, 키와 프로젝트 ID는 어디에도 나오지 않습니다.

```bash
uv run llm-probe --verbose --output probe-result.txt
```

첫 줄에 설정 요약이 나옵니다. `azure` 방식이면 `Azure 방식`, 프로젝트 ID를 넣었으면 `프로젝트 ID 있음`이 보여야 합니다.

### 점검 항목이 뜻하는 것

| 항목 | 이 도구에 | 기록할 것 |
|---|---|---|
| 1. 연결·모델 목록 | 인증이 되는지 보는 자리. `azure` 방식은 목록 API가 없어 `건너뜀(404)`이 나와도 정상 | `LLM_MODEL`이 목록에 있는지, 원문의 `max_model_len`과 `owned_by` |
| 2. 기본 채팅 | **필수** | 입력·출력 토큰 수 |
| 3. 스트리밍 | 참고용. llm-fix-server는 쓰지 않음 | 첫 토큰까지 시간 |
| 4. Tool calling | 참고용. 지금은 쓰지 않음 | ✅ / ⚠️ / ❌ |
| 5. JSON 출력 | **필수**. `json_schema`가 되어야 답을 구조화해 받음 | `json_schema`인지 `json_object`만인지, 걸린 시간 |

4번과 5번이 400 오류로 실패하면 "지원하지 않음"이라는 결과입니다. 고칠 대상이 아니니 그대로 기록합니다.

### 생각 모드

2번에서 짧은 답에 출력 토큰이 수십~수백 개 나오면 생각 모드가 켜진 것입니다. 끌 수 있는지 봅니다.

1. `.env`에 `LLM_ENABLE_THINKING=false`를 넣습니다.
2. `--output probe-result-think-off.txt`로 다시 돌립니다.
3. 두 결과에서 5번 걸린 시간과 원문의 `usage.completion_tokens`를 비교합니다. 차이가 없으면 끌 수 없는 서버입니다. 그래도 도구는 돌아갑니다.

### 컨텍스트 길이

1번 원문에 `max_model_len`이 없으면 직접 잽니다. 몇 분 걸립니다.

```bash
uv run llm-probe --context --output probe-result-context.txt
```

함수 하나와 프롬프트가 한 요청에 들어가므로 수만 토큰이면 충분하고, 클수록 긴 함수에 여유가 있습니다.

### 멈추면

네트워크·SSL·인증 오류가 나면 점검이 그 자리에서 멈추고 원인을 보여줍니다.

| 오류 | 확인할 것 |
|---|---|
| 서버에 접속할 수 없음 / 시간 초과 | 프록시 없이 직접 붙어야 하면 LLM 호스트를 `NO_PROXY`에 추가합니다. 프록시를 거쳐야 하면 `HTTPS_PROXY`를 설정합니다. |
| 인증서 검증 실패 | 사내 루트 인증서를 OS 인증서 저장소에 넣거나 `SSL_CERT_FILE=/경로/사내CA.pem`을 설정합니다. 둘 다 안 되는 자체 서명 인증서면 `LLM_VERIFY_SSL=false`를 넣습니다. |
| 인증 실패(401/403) | `LLM_API_KEY`. 비워 뒀다면 그 서버는 키가 필요하다는 뜻입니다. 프로젝트 ID를 받았다면 `LLM_PROJECT_ID`와 `LLM_PROJECT_HEADER`의 헤더 이름도 봅니다. |
| 경로를 찾을 수 없음(404) | `openai` 방식은 `LLM_BASE_URL` 끝의 `/v1`과 `LLM_MODEL`. `azure` 방식은 `LLM_CLIENT=azure`가 들어 있는지와 `LLM_BASE_URL`이 샘플의 base_url과 같은지 |

### 판정

2번과 5번(`json_schema`)이 ✅면 llm-fix-server를 쓸 수 있습니다. 나머지는 기록만 합니다.

## 3. 어떤 모델을 쓸지

### 후보 알아내기

- 1번에 모델 목록이 나오면 그 목록이 후보입니다.
- `azure` 방식처럼 목록이 없으면 담당자에게 쓸 수 있는 배포 이름을 받습니다.
- 임베딩 모델은 뺍니다. 지금은 쓰지 않습니다.

### 후보마다 점검 돌리기

`.env`는 그대로 두고 OS 환경변수로 모델만 바꿔 돌립니다. OS 환경변수가 `.env`보다 우선합니다.
결과 파일 이름에 모델명이 들어가니 그 파일은 PC 밖으로 내보내지 않습니다.

PowerShell에서는 끝난 뒤 환경변수를 지워야 다음 실행이 `.env`의 모델로 돌아갑니다.

```powershell
$env:LLM_MODEL = "후보 모델"; uv run llm-probe --verbose --output probe-후보.txt; Remove-Item Env:LLM_MODEL
```

bash:

```bash
LLM_MODEL="후보 모델" uv run llm-probe --verbose --output probe-후보.txt
```

후보마다 `.env.b`, `.env.c`처럼 파일을 두고 `uv run llm-probe --profile b`로 돌려도 됩니다. llm-fix-server도 `--profile b --port 8001`로 같은 폴더에서 하나 더 띄울 수 있어, 두 모델의 수정안을 나란히 비교할 때 씁니다.

### 비교 기준

| 기준 | 어디서 보나 | 보는 눈 |
|---|---|---|
| `json_schema` 지원 | 5번 | 안 되면 탈락 |
| 응답 시간 | 5번 걸린 시간 | 짧을수록 좋음. 한 작업에 수백 번 호출함 |
| 생각 모드 | 2번 출력 토큰, 끌 수 있는지 | 끌 수 있으면 빠름. 못 끄면 매 호출에 토큰이 더 듦 |
| 컨텍스트 길이 | `max_model_len` 또는 `--context` | 수만 토큰 이상 |
| 비용·쿼터 | 회사 안내 | 있다면 호출 수 × 토큰으로 어림 |
| 수정안 품질 | llm-fix-server로 같은 엑셀의 RTE 몇 행을 돌려 비교 | "확인 필요"가 적고 diff가 원인을 고치는 쪽 |

### 결정 기록

- 고른 모델은 `.env`의 `LLM_MODEL`에만 적습니다. 모델명은 저장소·채팅·캡처에 올리지 않습니다.
- 전달할 것: 후보 수, 탈락한 이유의 종류, 고른 이유(말로), 고른 모델의 5번 걸린 시간과 컨텍스트 길이.
