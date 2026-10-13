# 다른 파일의 정의를 LLM에 함께 보내기 설계

- 작성일: 2026-10-13
- 상태: 구현됨 (2026-10-13, master 5de19ff). 검토에서 typedef·struct·enum 정의를 범위에 넣음. 최종 검토 뒤 고친 것: 이름이 여럿인 정의는 요청·메모에서 한 번만 셈, `extern "C"` 묶음 헤더 지원, 매크로 꼴 서명의 이름 찾기, 지시문은 "numbered code"로 표현
- 구현 계획: `docs/superpowers/plans/2026-10-13-definitions-context.md`
- 바탕: `2026-10-08-sheet-kinds-misra-design.md`(종류 기반)와 `2026-10-07-rte-fixer-design.md`. 여기 적지 않은 것은 그대로 따른다.

## 1. 배경과 목표

RTE에 "직접 수정(manual)" 판단을 넣은 뒤 회사 PC에서 돌려 보니 Red·Orange의 대부분이 manual로 나왔다. 이유의 대부분은 "이 함수가 부르는 함수·매크로·변수의 정의가 보이지 않는다"였다.

원인은 보내는 코드의 범위다. 지금은 엑셀에 적힌 파일만 읽고, 그 파일에서 검사가 걸린 함수(파일이 2000줄 이하면 파일 전체)만 보낸다. 다른 파일의 함수 정의, 헤더의 매크로, 전역 배열의 크기는 LLM이 볼 수 없다. 지시문은 "보이지 않는 정의는 추측하지 말라"고 하고 manual 규칙은 "원인이 보여준 코드 밖이면 manual"이라고 하니, 규칙대로 manual이 된다.

목표: 보내는 코드가 쓰는 이름 가운데 다른 곳에 정의된 **함수, 매크로, 전역 변수, 타입(typedef·struct·union·enum)**의 정의를 찾아 요청에 함께 넣는다. 그러면 LLM이 값의 범위, 배열 크기, 반환값, 실제 타입을 보고 판단하고, 고칠 곳이 다른 파일이면 그 파일과 이름을 이유에 적을 수 있다.

MISRA에도 같은 문제가 있다. 다만 manual은 Mandatory에만 있어서, 정의가 안 보이면 Required·Advisory는 "수정 불필요"(일탈 근거)로 조용히 빠진다. MISRA는 필수 타입 규칙(10.x) 때문에 typedef의 실제 타입이 특히 필요하므로 타입 정의를 범위에 넣는다. CodeMetrics는 함수 하나를 나누는 일이라 영향이 거의 없다.

### 범위

포함:
- 소스 폴더 전체에서 정의 찾기(함수·매크로·전역 변수·타입)와 작업에 저장
- 요청마다 쓰는 이름을 골라 정의를 사용자 메시지에 덧붙이기
- RTE·MISRA 지시문에 정의의 쓰임과 한계를 적기
- DB 스키마 4, 작업 메모, 테스트

제외(나중에):
- 호출자(이 함수를 부르는 쪽) 코드 보내기
- 다른 파일을 고치는 패치. 이번에는 수정이 지금처럼 보여준 파일 안에만 있다
- CodeMetrics 종류. 함수 하나를 나누는 일이라 정의가 필요 없다. "수정 불필요" 이유에 정의 얘기가 보이면 같은 방식으로 붙인다

## 2. 동작 개요

1. 작업을 만들 때 소스 폴더 아래의 `.c`·`.h`를 모두 훑어 정의 목록을 만든다.
2. 분석 대상 파일(엑셀에 적힌 파일)의 글에 나오는 이름의 정의만 작업에 저장한다.
3. 묶음을 보낼 때, 보내는 코드 범위에 나오는 이름의 정의 가운데 그 범위 밖에 있는 것을 고른다. 검사가 걸린 줄 근처의 이름을 먼저, 개수와 줄 수에 상한을 두고 고른다.
4. 고른 정의를 코드 블록 뒤에 "다른 파일의 정의" 절로 붙인다. 참고용이며 수정 대상이 아님을 지시문에 적는다.
5. 작업 메모에 "참고 정의 N개"를 적는다. 같은 파일 안의 정의도 저장하므로 "다른 파일"이 아니라 "참고"라고 부른다.

예전 작업(스키마 3)은 정의가 없는 채로 그대로 보이고 이어서 처리된다.

## 3. 정의 찾기 (`csource.py`)

`fixer._top_level_blocks`(주석·문자열을 건너뛰며 깊이 0의 중괄호 블록을 찾는 스캐너)를 공용 모듈 `csource.py`로 옮기고, 같은 스캐너 위에 정의 찾기를 더한다. `fixer`는 옮긴 함수를 가져다 쓰고 같은 이름으로 다시 내보내 기존 테스트가 그대로 돈다. `csource`는 `fixer`를 가져오지 않는다(순환 방지).

파일 하나에서 찾는 것:

| 종류 | 찾는 법 | 저장 범위 |
|---|---|---|
| 매크로 | `#define 이름`으로 시작하는 줄. 줄 끝 `\`가 이어지면 다음 줄까지 | 그 줄들 |
| 함수 | 깊이 0 블록 위의 서명 줄(`select_region`과 같은 규칙으로 최대 10줄)에서 `(` 바로 앞의 식별자. 서명에 `=`가 있거나 `typedef`·`struct`·`union`·`enum`으로 시작하면 함수가 아니다 | 서명 첫 줄부터 닫는 중괄호까지 |
| 전역 변수 | 블록 밖 깊이 0에서 `;`로 끝나는 문장 가운데, `=`나 `[` 또는 `;` 바로 앞의 식별자. `(`가 있으면(원형) 뺀다. `typedef`·`struct`·`union`·`enum`으로 시작하면 타입이다. `= { … }` 초기화 블록이 있으면 블록 끝까지 | 선언 줄부터 문장 끝까지 |
| 타입 | `typedef`로 시작하는 문장은 `;` 바로 앞의 식별자(배열이면 `[` 앞, 함수 포인터면 `)(` 앞의 괄호 안 `*` 뒤). `struct`·`union`·`enum 태그 {`로 시작하는 블록은 태그 이름. 둘 다인 `typedef struct 태그 { … } 이름;`은 두 이름 모두 | 선언 줄부터 `;`까지 |

정의 하나는 `Definition(name, kind, file, first, last, text)`다. `kind`는 `function` / `macro` / `variable` / `type`이다. `text`는 줄 번호 없이 원문 그대로이고, `MAX_DEFINITION_LINES`(80)를 넘으면 앞 80줄 뒤에 `... (N more lines)` 한 줄을 붙여 자른다.

못 읽는 파일(UTF-8·CP949가 아니거나 줄바꿈이 섞임)은 건너뛰고 개수만 센다. 점으로 시작하는 폴더는 `list_files`처럼 건너뛴다.

## 4. 작업에 저장 (`store.py`, 스키마 4)

```sql
CREATE TABLE IF NOT EXISTS definitions (
    job_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,      -- function / macro / variable / type
    file TEXT NOT NULL,      -- 소스 폴더 기준 상대 경로
    first INTEGER NOT NULL,
    last INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, name, file, first)
);
```

- `create_job(..., definitions=[])`가 함께 넣는다. `Store.definitions(job_id)`가 이름순으로 돌려준다.
- 저장하는 것은 분석 대상 파일들의 글에 식별자로 나오는 이름의 정의뿐이다. 같은 파일 안의 정의도 저장한다(파일이 길어 함수만 보낼 때 쓴다).
- `SCHEMA_VERSION = 4`. 표는 `CREATE TABLE IF NOT EXISTS`로 더해지므로 옮기는 코드는 없다. 프로그램보다 새 DB를 거부하는 검사는 그대로다.

## 5. 요청마다 고르기 (`csource.pick_definitions`)

입력: 작업의 정의 목록, 보내는 소스와 범위(`first`~`last`), 묶음의 행.

1. 범위 안 글에서 식별자(`[A-Za-z_]\w*`)를 뽑고 C 예약어를 뺀다.
2. 이름이 정의 목록에 있는 것만 남긴다. 정의가 보내는 파일의 보내는 범위 안에 있으면 뺀다(이미 보인다).
3. 순서: 검사가 걸린 줄과 그 위아래 2줄에 나온 이름을 행 순서대로 먼저, 나머지는 범위 안 첫 등장 순서로.
4. 같은 이름의 정의가 여럿이면 같은 파일 → 헤더(`.h`) → 그 밖의 순서로 최대 2개.
5. 상한: `MAX_DEFINITIONS`(12)개, 합쳐서 `MAX_DEFINITION_TOTAL_LINES`(400)줄. 넘치면 뒤의 것을 버린다.

같은 입력에는 같은 출력이 나와야 한다(캐시 키가 메시지 전체이므로).

## 6. 메시지 (`fixer.build_prompt`, `make_request`)

`make_request(kind, findings, source, context, definitions=())`가 고른 정의를 받아 `Request.definitions`에 두고, 사용자 메시지의 코드 블록 뒤에 붙인다.

```text
Definitions from other places, for reference only. Edits must stay in the code above.

```c
// path/to/other.h lines 12-12
#define BUF_SIZE 16
```

```c
// path/to/other.c lines 40-58
int read_value(int index)
{
    ...
}
```
```

정의 블록에는 줄 번호 표시를 붙이지 않는다. 수정의 "original"은 번호가 붙은 코드에서만 베끼게 하기 위해서다. 고른 정의가 없으면 이 절은 붙지 않는다.

작업자는 `store.definitions(job_id)`를 읽어 `pick_definitions`로 고른 뒤 `make_request`에 넘긴다. 대화 기록은 메시지 전체를 적으므로 정의도 그대로 남는다.

## 7. 지시문

RTE와 MISRA의 시스템 지시문에 한 단락을 더한다(답 처리 방식은 그대로이므로 `prompt_version`은 올리지 않는다. 글이 바뀌므로 저장된 답은 자동으로 갈린다).

> Definitions of functions, macros, variables and types that this code uses but that live elsewhere may follow the code, for reference. Use them to judge value ranges, array sizes, return values and the real types behind typedefs. Edits must stay in the code shown; if the right change is in one of those definitions or in a caller, answer manual and name that file and function in reason.

RTE의 manual 규칙에서 "the cause is not in the code shown"은 "not in the code or the definitions shown"으로 바꾼다. "Do not guess definitions ... that are not shown"은 그대로 둔다.

## 8. 화면과 메모

- 작업 메모(`_job_note`)에 "참고 정의 N개"를 더한다. 못 읽은 파일이 있으면 "못 읽은 파일 M개"도 적는다.
- 결과 화면은 바뀌지 않는다.

## 9. 시간과 크기

- 폴더 훑기와 정의 찾기는 작업을 만드는 요청 안에서 한 번 한다. 큰 트리에서 수십 초가 걸리면 작업자 쪽으로 옮긴다. 현장에서 걸린 시간을 받는다.
- 저장하는 정의는 분석 대상 파일이 쓰는 이름으로 한정하고, 정의 하나는 80줄까지다.

## 10. 테스트

- `csource`: 함수(static, 포인터 반환, 여러 줄 서명), 여러 줄 매크로, 전역(초기화 블록, 배열, extern), 타입(typedef 한 줄, `typedef struct 태그 { } 이름;`은 두 이름, `enum 태그 { }`, 함수 포인터 typedef), 원형은 뺌, 주석·문자열 안의 중괄호
- 저장 필터: 분석 대상 파일이 쓰는 이름만, 같은 파일 정의도 포함, 80줄 자르기
- `pick_definitions`: 보내는 범위 안의 정의 제외, 검사 줄 근처 우선, 같은 이름 여러 정의의 순서와 2개 상한, 개수·줄 수 상한, 같은 입력에 같은 순서
- `make_request`: 정의가 있을 때만 절이 붙고, 캐시 키가 달라짐
- `store`: 스키마 4, 정의 저장과 읽기, 스키마 3 DB 열기
- `web`: 헤더의 매크로를 쓰는 두 파일 폴더로 작업을 만들면 요청에 그 매크로가 들어가고 메모에 개수가 보임
- `kinds`: RTE·MISRA 지시문에 정의 규칙이 있고 CodeMetrics에는 없음

테스트의 파일·함수·매크로 이름은 모두 지어낸 것이다.
