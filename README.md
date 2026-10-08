# llm-lab

회사 LLM(OpenAI 호환 API, 사내망 전용)의 연결·기능 점검 도구, 공용 클라이언트, Polyspace RTE 수정 제안 서비스입니다.

## 준비

1. 사내망(또는 VPN)에 연결합니다.
2. 의존성을 설치합니다.

   ```bash
   uv sync
   ```

3. 프로젝트 루트에서 `.env.example`을 `.env`로 복사하고 값을 채웁니다. `.env`는 git에 커밋되지 않습니다.

   | 변수 | 필수 | 설명 |
   |---|---|---|
   | `LLM_BASE_URL` | ✅ | 예: `https://llm.사내도메인/v1` (`/v1`까지만 적습니다. 끝에 `/chat/completions`가 붙어 있으면 자동으로 잘라냅니다) |
   | `LLM_API_KEY` | – | 발급받은 키. 키 없이 쓰는 서버면 비워 두며, 이때는 `Authorization` 헤더를 보내지 않습니다 |
   | `LLM_MODEL` | ✅ | 사용할 모델명 |
   | `LLM_TIMEOUT` | – | 요청 타임아웃(초), 기본 60 |
   | `LLM_VERIFY_SSL` | – | `false`이면 서버 인증서를 검증하지 않습니다(`requests`의 `verify=False`와 같음). 기본 `true` |
   | `LLM_ENABLE_THINKING` | – | `true`/`false`. 모든 채팅 요청에 `chat_template_kwargs.enable_thinking`을 붙입니다(생각 모드를 지원하는 모델용). 비워 두면 보내지 않습니다 |

   `LLM_VERIFY_SSL=false`는 중간자 공격을 막지 못하므로, 사내 루트 인증서를 OS 인증서 저장소에 넣을 수 없을 때만 사용하세요.

   OS 환경변수에 같은 이름이 있으면 `.env`보다 우선합니다. 키·주소는 항상 `LLM_*` 설정에서만 가져오고 조직·프로젝트 헤더는 보내지 않으므로, `OPENAI_API_KEY`·`OPENAI_BASE_URL`·`OPENAI_ORG_ID`·`OPENAI_PROJECT_ID`와 `OPENAI_CUSTOM_HEADERS`의 `Authorization`은 무시됩니다. 그 밖의 `OPENAI_CUSTOM_HEADERS` 항목은 그대로 요청에 붙습니다.

   `HTTP_PROXY`·`HTTPS_PROXY`·`NO_PROXY` 환경변수는 그대로 적용됩니다(Windows는 시스템 프록시 설정도 반영). 사내 LLM 호스트에 프록시 없이 직접 붙어야 하면 그 호스트를 `NO_PROXY`에 추가하고, 프록시를 거쳐야 하면 `HTTPS_PROXY`를 설정하세요.

## 점검 실행

```bash
uv run llm-probe
```

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

Polyspace Code Prover 결과 엑셀의 RTE 시트에서 Red/Orange 행마다 회사 LLM이 "수정" 또는 "수정 불필요"를 판단합니다. 수정이면 diff를 보여주고, 고른 수정을 패치 파일로 내려받아 적용합니다.

```bash
uv run llm-fix-server
```

1. 서버가 켜지면 브라우저가 `http://127.0.0.1:8000/`을 저절로 엽니다. 같은 PC에서만 열립니다. 포트를 바꾸려면 `--port 8080`, 브라우저를 열지 않으려면 `--no-browser`를 붙입니다.
2. 엑셀(.xlsx)을 고르고, 소스 코드가 들어 있는 최상위 폴더를 정한 뒤 "분석 시작"을 누릅니다.
   - 엑셀은 이름이 `_Result`로 끝나고 `TYPE`, `File`, `line`, `check`, `detail` 열이 있는 시트를 읽습니다.
   - "폴더 찾기…" 버튼을 누르면 윈도우 폴더 선택 창이 뜨고, 고른 폴더 경로가 칸에 채워집니다. 창이 안 보이면 작업 표시줄을 확인하세요.
   - 경로를 직접 붙여넣어도 됩니다(탐색기 주소창에서 복사, 따옴표가 붙어 있어도 됨).
   - 엑셀에 나온 파일만 그 폴더 안에서 찾아 읽습니다. 같은 이름 파일이 여러 개면 엑셀 경로와 폴더 구조가 가장 길게 맞는 것을 고르고, `.git`처럼 점으로 시작하는 폴더는 보지 않습니다.
   - 패치도 그 폴더 기준으로 만들어지므로 그 폴더에서 적용합니다.
   - 최근에 쓴 폴더는 입력 칸에서 목록으로 고를 수 있습니다.
3. 처리 중에는 결과 페이지가 진행 막대와 함께 5초마다 새로 고쳐집니다. 끝나면 적용할 수정에 체크하거나 "수정 모두 선택"을 누른 뒤 "패치 내려받기"를 누릅니다.
4. 화면에 나온 폴더에서 패치를 확인한 뒤 적용합니다. `core.autocrlf=false`는 Git for Windows가 LF 파일을 CRLF로 바꾸지 않게 합니다.

```bash
git -c core.autocrlf=false apply --check fixes.patch
```

```bash
git -c core.autocrlf=false apply fixes.patch
```

- 소스는 UTF-8 또는 CP949여야 하고, 한 파일 안의 줄바꿈은 한 가지(CRLF 또는 LF)여야 합니다.
- 작업과 결과는 `private/fixer.db`에 저장되며 커밋되지 않습니다. 같은 파일·같은 지적은 LLM을 다시 부르지 않습니다.
- LLM과 오간 대화는 행마다 `private/llm-logs/시각_job작업번호_row엑셀행.md`로 남습니다. 보낸 메시지(코드 포함), 받은 응답, 응답 원문 JSON이 그대로 들어 있고 API 키는 없습니다. 이전 결과를 재사용해 LLM을 부르지 않은 행도 그렇다고 적힌 파일이 남습니다.
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
