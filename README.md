# llm-lab

회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace 수정 제안 서비스(RTE·MISRA·CodeMetrics)입니다.

## 준비

회사 PC에서 처음 설정할 때는 [초기 시작 구성](docs/company-checklist.md)을 따릅니다. 코드 받기부터 `.env` 작성, 되는지 점검, 모델 고르기까지 순서대로 적혀 있습니다. 아래는 그 요약입니다.

1. 사내망(또는 VPN)에 연결합니다.
2. 의존성을 설치합니다.

   ```bash
   uv sync
   ```

3. 프로젝트 루트에서 `.env.example`을 `.env`로 복사하고 값을 채웁니다. `.env`는 git에 커밋되지 않습니다.

   | 변수 | 필수 | 설명 |
   |---|---|---|
   | `LLM_BASE_URL` | ✅ | 예: `https://llm.사내도메인/v1` (`/v1`까지만 적습니다. 끝에 `/chat/completions`가 붙어 있으면 자동으로 잘라냅니다. `LLM_CLIENT=azure`면 회사 샘플 코드의 base_url 값을 그대로 적습니다) |
   | `LLM_API_KEY` | – | 발급받은 키. 키 없이 쓰는 서버면 비워 두며, 이때는 `Authorization` 헤더를 보내지 않습니다 |
   | `LLM_PROJECT_ID` | – | 키와 별도로 프로젝트 ID를 요구하는 게이트웨이면 채웁니다. 비워 두면 프로젝트 헤더를 보내지 않습니다 |
   | `LLM_PROJECT_HEADER` | – | 프로젝트 ID를 담을 헤더 이름. 게이트웨이 안내문에 적힌 이름을 그대로 적습니다. 기본 `OpenAI-Project` |
   | `LLM_CLIENT` | – | `azure`면 회사 샘플 코드처럼 `AzureOpenAI`로 접속합니다. 키는 `api-key` 헤더로 가고 경로는 `<LLM_BASE_URL>/openai/deployments/<모델>/chat/completions`가 됩니다. 비워 두면 `openai`(Bearer 키, `<LLM_BASE_URL>/chat/completions`) |
   | `LLM_API_VERSION` | – | `azure` 방식의 `api-version` 쿼리. 비워 두면 붙이지 않습니다 |
   | `LLM_MODEL` | ✅ | 사용할 모델명 |
   | `LLM_TIMEOUT` | – | 요청 타임아웃(초), 기본 60 |
   | `LLM_VERIFY_SSL` | – | `false`이면 서버 인증서를 검증하지 않습니다(`requests`의 `verify=False`와 같음). 기본 `true` |
   | `LLM_ENABLE_THINKING` | – | `true`/`false`. 모든 채팅 요청에 `chat_template_kwargs.enable_thinking`을 붙입니다(생각 모드를 지원하는 모델용). 비워 두면 보내지 않습니다 |

   `LLM_VERIFY_SSL=false`는 중간자 공격을 막지 못하므로, 사내 루트 인증서를 OS 인증서 저장소에 넣을 수 없을 때만 사용하세요.

   OS 환경변수에 같은 이름이 있으면 `.env`보다 우선합니다. 키·주소·프로젝트 ID는 항상 `LLM_*` 설정에서만 가져오고 조직 헤더는 보내지 않으므로, `OPENAI_API_KEY`·`OPENAI_BASE_URL`·`OPENAI_ORG_ID`·`OPENAI_PROJECT_ID`와 `OPENAI_CUSTOM_HEADERS`의 `Authorization`은 무시됩니다. `azure` 방식에서도 `AZURE_OPENAI_*`와 `OPENAI_API_VERSION`은 읽지 않습니다. 그 밖의 `OPENAI_CUSTOM_HEADERS` 항목은 그대로 요청에 붙습니다.

   `HTTP_PROXY`·`HTTPS_PROXY`·`NO_PROXY` 환경변수는 그대로 적용됩니다(Windows는 시스템 프록시 설정도 반영). 사내 LLM 호스트에 프록시 없이 직접 붙어야 하면 그 호스트를 `NO_PROXY`에 추가하고, 프록시를 거쳐야 하면 `HTTPS_PROXY`를 설정하세요.

## 점검 실행

```bash
uv run llm-probe
```

점검 항목이 이 도구에 무엇을 뜻하는지와 여러 모델을 비교하는 방법은 [초기 시작 구성](docs/company-checklist.md)의 2절과 3절에 있습니다.

명령은 프로젝트 루트(`.env`가 있는 폴더)에서 실행합니다. `.env`는 현재 폴더에서만 읽습니다.

응답 원문 JSON까지 보려면 `--verbose`를 붙입니다. 결과를 파일로 남기려면 `--output probe-result.txt`를 붙입니다. 화면과 같은 내용이 UTF-8로 저장되므로 `tee`나 리다이렉트가 필요 없습니다. API 키는 어떤 출력에도 나오지 않습니다.

모델 목록에 컨텍스트 길이(`max_model_len`)가 없으면 `--context`로 직접 잽니다. 약 8천 토큰짜리 프롬프트부터 두 배씩 늘려 25만 6천 토큰까지 보내고, 서버가 거부하면 그 자리에서 멈추고 서버 메시지를 보여줍니다. 메시지에 보통 정확한 최대값이 들어 있습니다. 요청마다 최대 5분까지 기다리므로 몇 분 걸릴 수 있습니다.

```bash
uv run llm-probe --context --output probe-result-context.txt
```

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

## 수정 제안 서비스 (llm-fix-server)

Polyspace 결과 엑셀의 RTE 시트(Red/Orange), MISRA C:2012 시트, CodeMetrics 시트에서 행마다 회사 LLM이 "수정", "수정 불필요", "직접 수정"(고쳐야 하지만 보인 코드로는 못 고쳐 사람이 정해야 함) 중 하나를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.

```bash
uv run llm-fix-server
```

1. 서버가 켜지면 브라우저가 `http://127.0.0.1:8000/`을 저절로 엽니다. 같은 PC에서만 열립니다. 포트를 바꾸려면 `--port 8080`, 브라우저를 열지 않으려면 `--no-browser`를 붙입니다.
2. 엑셀(.xlsx)을 고르고, 소스 코드가 들어 있는 최상위 폴더와 분석할 시트를 정한 뒤 "분석 시작"을 누릅니다.
   - "분석할 시트"에서 RTE나 MISRA를 고르고 분석할 대상을 체크합니다. RTE는 Red·Orange, MISRA는 Mandatory·Required·Advisory이고, MISRA는 처음에 Mandatory와 Required만 체크되어 있습니다.
   - 엑셀은 이름이 `_Result`로 끝나고 종류 이름(`rte`, `misra`)이 들어간 시트를 읽습니다. 그 시트에 `TYPE`, `File`, `line`, `check`, `detail` 열이 있어야 합니다.
   - MISRA는 엑셀의 조건 시트(이름은 상관없고 `Guideline`, `Mode` 열이 있는 시트)에서 규칙마다 분류(`Mode`)와 적용 여부(`Enabled`)를 작업마다 읽습니다. 꺼진 규칙은 빼고, 분류는 조건 시트를 따릅니다. 조건 시트가 없으면 결과 시트의 `Category`를 씁니다.
   - CodeMetrics는 이름에 `metric`이 들어간 `_Result` 시트에서 `File`, `Function`, `line`, `Check`, `Threshold`, `Actual Value` 열을 읽습니다. 대상은 Fail·가운데·단계 모름이고 처음에 모두 체크되어 있습니다.
   - CodeMetrics의 단계는 엑셀의 조건 시트(이름은 상관없고 범위 열과 판정 열이 있는 시트)에서 작업마다 읽고, `Actual Value`가 든 범위로 정합니다. 조건 시트상 Pass인 행과 `Function`이 빈 행은 뺍니다. 결과 시트와 조건 시트의 지표 이름이 다르면 낱말로, 그래도 안 되면 한도 값으로 짝짓고, 결과는 작업 메모에 개수로 보입니다.
   - "폴더 찾기…" 버튼을 누르면 윈도우 폴더 선택 창이 뜨고, 고른 폴더 경로가 칸에 채워집니다. 창이 안 보이면 작업 표시줄을 확인하세요.
   - 경로를 직접 붙여넣어도 됩니다(탐색기 주소창에서 복사, 따옴표가 붙어 있어도 됨).
   - 엑셀에 나온 파일만 그 폴더 안에서 찾아 읽습니다. 같은 이름 파일이 여러 개면 엑셀 경로와 폴더 구조가 가장 길게 맞는 것을 고르고, `.git`처럼 점으로 시작하는 폴더는 보지 않습니다.
   - 패치도 그 폴더 기준으로 만들어지므로 그 폴더에서 적용합니다.
   - 최근에 쓴 폴더는 입력 칸에서 목록으로 고를 수 있습니다.
3. 처리 중에는 결과 페이지가 진행 막대와 함께 5초마다 새로 고쳐집니다. 결과는 탭 두 개로 봅니다.
   - RTE는 같은 파일의 같은 함수에 걸린 Red·Orange를 묶어 LLM에 한 번에 보냅니다. 같은 줄의 수정이 서로 겹치지 않고, 한 원인에서 나온 여러 검사를 한 번에 고칩니다.
   - RTE 수정안은 보인 코드 안에서 원인을 없앨 수 있을 때만 냅니다(초기화, 실제 배열 크기, 값이 들어오는 자리의 검사, 루프 경계). 기본값 대입, 연산 건너뛰기, 조기 return, 항상 참인 조건으로 가리는 수정은 하지 않고, 그런 경우와 원인이 안 보이는 호출자·정의에 있는 경우는 "직접 수정"으로 표시하고 무엇을 어디서 정해야 하는지 적습니다. MISRA Mandatory도 보인 코드로 부족하면 "직접 수정"입니다.
   - MISRA는 같은 파일의 같은 줄에 걸린 지적을 묶어 LLM에 한 번에 보냅니다. 판단과 이유는 행마다 받고, 수정은 묶음이 함께 씁니다.
   - CodeMetrics는 같은 함수에 걸린 지표를 묶어 함수마다 LLM에 한 번 보냅니다. LLM은 그 함수(바로 위 주석 포함)를 통째로 다시 쓰고, 서버는 바뀐 줄만 남겨 diff와 패치를 만듭니다. 함수 이름·인자·반환형이 바뀌었거나 위 주석이 빠졌으면 받지 않고 한 번 다시 묻습니다. Number of Calling Functions(부르는 곳 수)는 다른 파일을 바꿔야 하므로 늘 "수정 불필요"입니다.
   - 중요한 대상(Red, Mandatory)부터 처리합니다. "멈춤"을 누르면 그때까지 처리된 행으로 패치를 받을 수 있고, "이어서 처리"로 남은 행을 계속합니다.
   - "한눈에 보기": 맨 위 개수(Red/Orange, 수정 / 수정 불필요 / 직접 수정 / 확인 필요), 검사 종류별·파일별 표, 행마다 한 줄짜리 표. "보기"를 누르면 그 행의 diff로 갑니다.
   - "자세히 보기 (diff)": 행마다 판단 근거와 diff
   - "확인 필요"는 LLM이 처리하지 못했거나 소스를 찾지 못한 행입니다. 무엇이 문제인지와 할 일을 두 줄로 보여주고, 개발자용 내용과 대화 기록 파일 이름은 "자세히 (개발자용)"을 펼치면 보입니다.
4. 작업이 끝나면 적용할 수정에 체크하거나 "수정 모두 선택"을 누른 뒤 "패치 내려받기"를 누릅니다.
   - 서로 다른 수정이 같은 줄을 고치면 함께 적용할 수 없습니다. "수정 모두 선택"은 중요한 대상(Red, Mandatory)을 먼저, 그다음 엑셀 행 순서로 겹치지 않는 것만 고릅니다.
   - CodeMetrics의 가운데 단계 수정은 "수정 모두 선택"이 고르지 않습니다. diff를 검토한 뒤 직접 체크하세요. 같은 함수의 수정은 하나라서, 그 함수의 행 하나만 골라도 리팩터링 전체가 패치에 들어갑니다.
   - 빠진 행에는 "이번엔 제외 · 행 N 수정과 같은 줄을 고칩니다"가 붙습니다. 직접 체크하면 겹치는 상대 행의 체크가 풀립니다.
   - 빠진 행은 패치를 적용하고 Polyspace를 다시 돌린 뒤, 새 엑셀로 다시 분석하면 됩니다. 이미 고쳐졌으면 다음 결과에 나오지 않습니다.
5. 화면에 나온 폴더에서 패치를 확인한 뒤 적용합니다. `core.autocrlf=false`는 Git for Windows가 LF 파일을 CRLF로 바꾸지 않게 합니다.

```bash
git -c core.autocrlf=false apply --check fixes.patch
```

```bash
git -c core.autocrlf=false apply fixes.patch
```

- 소스는 UTF-8 또는 CP949여야 하고, 한 파일 안의 줄바꿈은 한 가지(CRLF 또는 LF)여야 합니다.
- 작업과 결과는 `private/fixer.db`에 저장되며 커밋되지 않습니다. 같은 파일·같은 지적은 LLM을 다시 부르지 않습니다.
- LLM과 오간 대화는 묶음마다 `private/llm-logs/시각_job작업번호_row엑셀행.md`로 남습니다(엑셀 행은 묶음의 가장 작은 행). 보낸 메시지(코드 포함), 받은 응답, 응답 원문 JSON이 그대로 들어 있고 API 키는 없습니다. 이전 결과를 재사용해 LLM을 부르지 않은 행도 그렇다고 적힌 파일이 남습니다.
- 서비스를 껐다 켜면 처리 중이던 작업을 이어서 합니다.
- 주소: 새 분석 `/`, 작업 목록 `/results`, 작업 결과 `/results/{작업 번호}`, 결과 JSON `/api/results/{작업 번호}`.

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
