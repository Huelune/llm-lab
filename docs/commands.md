# 명령 모음

자주 쓰는 명령을 할 일별로 모았습니다. 자세한 설명은 [README](../README.md)와 [초기 시작 구성](company-checklist.md)에 있습니다.

- 모든 명령은 프로젝트 루트(`.env`가 있는 폴더)에서 실행합니다.
- `--profile 이름`을 붙이면 `.env` 대신 `.env.이름`을 읽고, 작업 DB와 대화 기록은 `private/이름/`에 둡니다. 프로필로 켠 서버의 기록을 보거나 지울 때도 같은 `--profile 이름`을 붙입니다.

## 설치·최신 코드

```bash
git pull
```

```bash
uv sync
```

## LLM 연결 점검 (llm-probe)

| 명령 | 하는 일 |
|---|---|
| `uv run llm-probe` | 연결, 기본 채팅, 스트리밍, Tool calling, JSON 출력을 차례로 점검 |
| `uv run llm-probe --verbose` | 응답 원문 JSON까지 보여 줌 |
| `uv run llm-probe --output probe-result.txt` | 화면과 같은 내용을 UTF-8 파일로도 저장 |
| `uv run llm-probe --context` | 최대 입력 토큰을 직접 잼(몇 분 걸림) |
| `uv run llm-probe --parallel 4` | 짧은 요청 4개(1~16)를 동시에 보내 성공·429·오류 수와 시간을 셈. 화면에 숫자만 나와 캡처해도 됨. 결과에 맞춰 `.env`의 `LLM_WORKERS`를 정함 |
| `uv run llm-probe --profile 이름` | `.env.이름`의 설정으로 점검 |

점검 화면에는 주소와 모델 이름이 나오므로 그대로 캡처해 보내지 않습니다.

## 수정 제안 서버 (llm-fix-server)

| 명령 | 하는 일 |
|---|---|
| `uv run llm-fix-server` | 서버를 켜고 브라우저로 `http://127.0.0.1:8000/`을 엶. 끄려면 Ctrl+C |
| `uv run llm-fix-server --port 8001` | 다른 포트로 켬 |
| `uv run llm-fix-server --no-browser` | 브라우저를 자동으로 열지 않음 |
| `uv run llm-fix-server --profile 이름 --port 8001` | 설정이 다른 두 번째 서버를 켬(모델 비교용) |

### 기록 지우기

서버를 끈 상태에서 실행합니다. 켜져 있으면 "서버를 끈 뒤 다시 실행하세요"가 나옵니다.

| 명령 | 지우는 것 | 화면 |
|---|---|---|
| `uv run llm-fix-server --clear all` | 작업 목록, 저장된 LLM 답, 대화 기록 | `N files deleted` |
| `uv run llm-fix-server --clear cache` | 저장된 LLM 답만(작업 목록은 남음) | `N cached answers deleted` |
| `uv run llm-fix-server --profile 이름 --clear all` | 그 프로필의 기록만 | `N files deleted` |

## 패치 적용·되돌리기

결과 화면의 "패치 적용 방법"에 나온 폴더에서, 확인을 먼저 하고 적용합니다.

```bash
git -c core.autocrlf=false apply --check fixes.patch
```

```bash
git -c core.autocrlf=false apply fixes.patch
```

되돌릴 때는 적용한 것과 같은 fixes.patch로, 같은 폴더에서 `-R`을 붙여 확인한 뒤 실행합니다. 적용한 뒤 그 줄을 손으로 고쳤으면 확인 단계에서 멈춥니다.

```bash
git -c core.autocrlf=false apply -R --check fixes.patch
```

```bash
git -c core.autocrlf=false apply -R fixes.patch
```

## 살펴보기 도구 (읽기만 함)

모두 개수와 표준 용어만 찍으므로 캡처해 보내도 됩니다.

| 명령 | 하는 일 |
|---|---|
| `uv run python scripts/peek_conditions.py "엑셀.xlsx" [misra] [metric] [rte]` | 엑셀의 조건 시트를 한 화면에 요약 |
| `uv run python scripts/peek_metrics.py "엑셀.xlsx"` | CodeMetrics 시트의 지표별 행 수 |
| `uv run python scripts/peek_job.py [패턴] [--job 번호] [--profile 이름]` | 작업 하나가 정의·호출자를 왜 못 붙였는지 개수로 셈 |
| `uv run python scripts/peek_logs.py [패턴] [--job 번호] [--profile 이름]` | 작업 하나의 판단과 "직접 수정" 행, 그 이유가 짚은 이름이 요청에 붙었는지, 정의 되묻기 수를 대화 기록에서 셈 |
| `uv run python scripts/peek_misra.py "엑셀.xlsx"` | MISRA 결과 행을 규칙 장별로, 조건 시트에서 꺼짐·분류별로 셈 (장 번호와 개수만) |

`--job`을 빼면 가장 최근 작업을 봅니다.

## 개발

```bash
uv run pytest
```

```bash
uv run ruff check
```
