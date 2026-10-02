# 회사에서 할 일

2026-10-02 기준. 2단계(규칙 문서 기반 정적분석·수정 API) 설계를 시작하려면 아래 두 가지가 필요합니다.

1. LLM에 접속되는 서버에서 `llm-probe`를 돌린 결과
2. 질문지 답변과 규칙 엑셀 샘플

## 1. 서버에 코드 가져가기

- [ ] 서버에서 github.com에 접속되는지 확인합니다.
  - 접속되면 clone합니다. 비공개 저장소이므로 `gh auth login`이나 개인 액세스 토큰이 필요합니다.

    ```bash
    git clone https://github.com/Huelune/llm-lab.git
    ```

  - 접속되지 않으면 개발 PC에서 ZIP을 만들어 서버로 복사합니다.

    ```bash
    git archive -o llm-lab.zip HEAD
    ```

- [ ] `uv`와 Python 3.14를 준비합니다. `uv`가 있으면 `uv python install 3.14`로 Python도 설치됩니다.
- [ ] PyPI에 접속되지 않으면 사내 미러 주소를 확인해 `UV_DEFAULT_INDEX`에 넣습니다. 미러가 없으면 그 사실을 기록해 둡니다. 질문지 3번의 답이 되고, 오프라인 설치 방법을 따로 준비해야 합니다.
- [ ] 의존성을 설치합니다.

  ```bash
  uv sync
  ```

## 2. `.env` 작성

- [ ] `.env.example`을 `.env`로 복사하고 `LLM_BASE_URL`(`/v1`까지 포함)과 `LLM_MODEL`을 채웁니다. 키가 있는 서버면 `LLM_API_KEY`도 채웁니다.
- [ ] 예전에 `requests`로 호출하던 코드가 있다면 같은 설정을 맞춥니다.
  - `verify=False`를 썼다면 `LLM_VERIFY_SSL=false`
  - `"chat_template_kwargs": {"enable_thinking": False}`를 보냈다면 `LLM_ENABLE_THINKING=false`
  - 그 코드의 `model` 값을 `LLM_MODEL`에 넣습니다.
- [ ] 키는 채팅이나 스크린샷에 올리지 않습니다.

## 3. 점검 실행

결과를 파일로 남겨 둡니다. Linux에서는 이렇게 실행합니다.

```bash
uv run llm-probe --verbose 2>&1 | tee probe-result.txt
```

Windows PowerShell에서 파일로 저장할 때는 먼저 `$env:PYTHONUTF8 = "1"`을 설정해야 ✅ 같은 기호가 깨지지 않습니다. API 키는 어떤 출력에도 나오지 않습니다.

## 4. 결과에서 볼 것

| 항목 | 기록할 것 | 2단계에서 쓰는 곳 |
|---|---|---|
| 1. 연결·모델 목록 | `LLM_MODEL`이 목록에 있는지. 원문 JSON의 `max_model_len`과 `owned_by` 값 | `max_model_len`은 컨텍스트 길이(질문지 1번)입니다. `owned_by`가 `vllm`이면 서빙 엔진이 vLLM입니다. |
| 2. 기본 채팅 | 입력·출력 토큰 수 | 요청 비용 계산 |
| 3. 스트리밍 | 첫 토큰까지 시간 | 응답 속도 기대치 |
| 4. Tool calling | ✅ / ⚠️ / ❌ | 검사 결과를 구조화해서 받는 방식을 정합니다. |
| 5. JSON 출력 | `json_schema`가 되는지, `json_object`만 되는지 | 4번과 함께 위와 같습니다. |

4번과 5번이 400 오류로 실패하면 "지원하지 않음"이라는 결과이므로 고칠 대상이 아닙니다. 그대로 기록합니다.

## 5. 중간에 멈추면

네트워크·SSL·인증 오류가 나면 점검이 그 자리에서 멈추고 원인을 보여줍니다.

| 오류 | 확인할 것 |
|---|---|
| 서버에 접속할 수 없음 / 시간 초과 | 프록시 없이 직접 붙어야 하면 LLM 호스트를 `NO_PROXY`에 추가합니다. 프록시를 거쳐야 하면 `HTTPS_PROXY`를 설정합니다. |
| 인증서 검증 실패 | 사내 루트 인증서를 OS 인증서 저장소에 넣거나 `SSL_CERT_FILE=/경로/사내CA.pem`을 설정합니다. 둘 다 안 되는 자체 서명 인증서면 `LLM_VERIFY_SSL=false`를 넣습니다. |
| 인증 실패(401/403) | `LLM_API_KEY`. 비워 뒀다면 그 서버는 키가 필요하다는 뜻입니다. |
| 경로를 찾을 수 없음(404) | `LLM_BASE_URL` 끝의 `/v1`과 `LLM_MODEL` 이름 |

## 6. 질문지 전달

- [ ] [사내 LLM 도입 확인 질문지](https://claude.ai/code/artifact/26621ad3-f802-4dc6-b364-3cd337beae15)를 담당자에게 공유합니다. 지금은 비공개 상태이므로 공유 설정을 바꾸거나 Word/PDF로 내보내서 전달합니다.
- [ ] "꼭 물어볼 것" 5개를 먼저 받습니다. 특히 아래 세 가지는 답에 따라 구조가 달라집니다.
  - 2번: 소스코드를 LLM에 보내도 되는지, 로그가 남는지
  - 3번: LLM에 접속되는 서버에 API 서비스를 올릴 수 있는지
  - 엑셀 샘플을 미리 받을 수 있는지
- [ ] 4단계에서 `max_model_len`을 확인했다면 컨텍스트 길이 질문은 건너뛰어도 됩니다.

## 7. 가져올 것

- [ ] `probe-result.txt`
- [ ] 질문지 답변
- [ ] 규칙 엑셀 샘플. 몇 행만 있어도 됩니다.
- [ ] 규칙 위반이 들어 있는 샘플 코드와 기대 결과(있으면)

이것들이 모이면 2단계 설계를 시작합니다.
