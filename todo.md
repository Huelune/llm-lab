# 할 일

회사 PC(LLM에 접속되는 Windows PC)에서 해볼 것들입니다. 결과를 알려주시면 Claude가 그 항목을 지웁니다.

회사 자료(점검 결과, 엑셀, 소스코드)는 커밋하지 않습니다. Claude에게는 아래에 적힌 것만 알려주면 되고, 주소·모델 이름·파일 경로는 가려도 됩니다.

## 1. 컨텍스트 길이 재기

최신 코드를 받고 실행합니다. 몇 분 걸릴 수 있습니다.

```bash
git pull
```

```bash
uv run llm-probe --context --output probe-result-context.txt
```

알려줄 것:

- 마지막 `결과:` 줄
- 거부된 단계 메시지에 나온 숫자(예: `maximum context length is 32768 tokens`)
- 413 같은 다른 오류가 나왔다면 그 메시지

## 2. 대상 언어

- C인지 C++인지
- MISRA 규칙 버전(예: MISRA C:2012, MISRA C:2023, MISRA C++:2008). MISRA 시트에 적혀 있을 수 있습니다.

## 3. RTE 시트 `File` 칸 형식

`File` 칸에 경로가 어떻게 적혀 있는지 알려줍니다. 셋 중 어느 쪽인지만 알면 됩니다. 폴더 이름은 가려도 되고, 형태만 보이면 됩니다(예: `C:\***\***\src\***.c`).

- 전체 경로(예: `C:\work\proj\src\a.c`)
- 상대 경로(예: `src\a.c`)
- 파일 이름만(예: `a.c`)

패치 파일 안의 경로와 `git apply`를 실행할 폴더가 이것으로 정해집니다.

## 4. 수정 제안 서비스 실제로 써 보기

회사 PC에서 최신 코드를 받고 서비스를 켭니다.

```bash
git pull
```

```bash
uv sync
```

```bash
uv run llm-fix-server
```

1. 브라우저에서 `http://127.0.0.1:8000`을 열고 Polyspace 엑셀과 해당 소스 파일들을 올립니다.
2. 결과 화면에서 판단·이유·diff가 읽을 만한지 봅니다. LLM에 무엇을 보내고 받았는지는 `private/llm-logs/`의 `.md` 파일에서 볼 수 있습니다.
3. 몇 개를 골라 패치를 받고, 화면에 나온 폴더에서 `git -c core.autocrlf=false apply --check fixes.patch` 다음 `--check` 없이 적용합니다.
4. Polyspace를 다시 돌립니다.

알려줄 것(파일 경로·코드는 가려도 됩니다):

- 처리한 행 수와 판단 분포(수정 / 수정 불필요 / 오류 / 소스 없음)
- 이상하다고 느낀 판단이나 오류 메시지 예시 1~2개
- `git apply`가 됐는지
- Polyspace 재실행 결과: "수정" 행이 Green이 됐는지, 새 Red/Orange가 생겼는지

## 나중에 (RTE 다음)

- MISRA와 CodeMetrics 시트의 이름, 헤더 행, 가린 예시 행 1~2개
- Polyspace 버전(예: R2023b)
- 이 엑셀을 누가, 무엇으로 만드는지. 사내 스크립트나 매크로인지, 다른 도구인지 확인합니다. Polyspace는 엑셀을 직접 만들지 못하므로 중간에 변환하는 무언가가 있습니다.
- CodeMetrics 시트도 고칠 대상인지. 지적이 아니라 함수 복잡도·줄 수 같은 측정값 목록이라서, 고치려면 버그 수정이 아니라 함수 쪼개기 같은 리팩터링이 됩니다.
