# RTE 요청에 호출자 코드 함께 보내기 설계

- 작성일: 2026-10-14
- 상태: 사용자 검토 대기
- 바탕: `2026-10-13-definitions-context-design.md`(참고 정의), `2026-10-14-cross-file-fixes-design.md`(다른 파일 수정). 여기 적지 않은 것은 그대로 따른다.

## 1. 배경과 목표

RTE의 원인은 대개 "이 함수에 들어오는 값"이고, 그 값은 함수를 부르는 쪽(호출자)이 정한다. 지금 요청에는 검사가 걸린 함수와, 그 함수가 부르는 쪽의 정의만 들어간다. 그래서 LLM은 들어오는 값을 모른 채 가드를 넣거나 "원인이 호출자에 있음"으로 직접 수정을 낸다.

목표: RTE 요청에 **검사가 걸린 함수를 부르는 곳**(호출 지점 주변 코드)을 함께 보낸다. LLM은 실제로 들어오는 값을 보고 "수정 불필요"를 판단하거나, 호출자 쪽에서 값을 거르는 근본 수정을 낼 수 있다. 호출자 수정은 이미 있는 "다른 파일 수정" 장치(칩, 자동 선택에서 빼기, 패치)에 그대로 실린다.

### 범위

포함:
- 프로젝트 전체에서 검사 함수의 호출 지점 찾기(직접 호출, 그리고 그 함수를 가리키는 별칭 매크로를 통한 호출)
- 호출 지점마다 주변 줄을 "호출자 조각"으로 작업에 저장하고, 그 파일 원문도 저장
- 요청에 "호출자" 절을 붙이고, 고칠 수 있게 표시(줄 번호)
- RTE 지시문에 호출자의 쓰임 추가, 작업 메모에 개수

제외:
- 함수 포인터·콜백으로 부르는 곳. 이름으로 찾을 수 없다.
- 호출자의 호출자(2단계 이상). 결과를 보고 정한다.
- MISRA·CodeMetrics.

## 2. 동작 개요

1. 작업을 만들 때 프로젝트 폴더의 `.c`를 모두 읽는다(정의 찾기와 같은 한 번의 훑기에서).
2. 분석 대상 행마다 검사 줄을 품은 함수의 이름을 정한다(그 줄을 품은 최상위 블록의 함수 이름. 못 찾으면 엑셀 Function 칸).
3. 그 이름들을 부르는 곳을 프로젝트 전체에서 찾는다. 그 함수 자신 안의 재귀 호출은 뺀다.
4. 호출 지점마다 그 줄의 위아래 `CALLER_CONTEXT`(10)줄을, 호출자 함수의 범위 안으로 잘라 조각 하나로 만든다. 같은 호출자 안에서 조각이 겹치면 합친다.
5. 조각을 작업에 저장하고(정의 표에 종류 `caller`로), 조각이 든 파일 원문도 작업 파일로 저장한다.
6. 요청마다 그 묶음의 검사 함수에 대한 조각을 골라 "Callers" 절로 붙인다. 같은 파일 먼저, 그다음 파일 이름·줄 순서로, 요청당 `MAX_CALLERS`(4)곳, 합쳐서 `MAX_CALLER_LINES`(120)줄까지.
7. 조각은 고칠 수 있게 줄 번호를 붙여 보낸다. 조각 범위 밖은 고칠 수 없다.

## 3. 찾기 (`csource.py`)

- `enclosing_function(text, line) -> str | None`: 그 줄을 품은 최상위 블록의 함수 이름(`find_definitions`와 같은 서명 규칙).
- `find_call_sites(file, text, names) -> list[Definition]`: 파일의 함수 블록마다, 주석·문자열을 지운 글에서 `이름(` 꼴을 찾는다. 이름이 `names`에 있고 그 함수 자신의 이름이 아니면 호출 지점이다. 조각은 `Definition(name=<부른 이름>, kind="caller", file, first, last, text)`이다. `name`은 별칭을 거쳤으면 검사 함수의 이름으로 적는다.
- 별칭: 정의 목록에서 `macro_target(d) == 검사 함수`인 매크로 이름도 `names`에 더한다(1단계).
- 조각의 `text`는 원문 그대로, 위에 호출자 함수 이름을 붙이지 않는다(헤더 주석에 적는다).

## 4. 저장

- DB 표는 바뀌지 않는다. 조각은 `definitions` 표에 `kind = "caller"`로 들어간다.
- 조각이 든 파일 원문을 `files` 표에 더한다(다른 파일 수정과 같은 방식).
- `pick_definitions`는 `caller`를 고르지 않는다(이름이 검사 함수라 보낸 코드에 늘 나오기 때문).
- 작업 메모: "호출 지점 N곳".

## 5. 요청

- `Kind.sends_callers: bool = False`. RTE만 `True`.
- 묶음의 검사 함수 이름은 행들의 검사 줄에서 `enclosing_function`으로 정한다.
- 절 모양:

```text
Callers of divide(), for the values that reach its parameters. Those marked editable may be changed; set "file" to their path.

```c
// editable caller: app/main.c lines 40-60 (in update())
   40| ...
```
```

- 조각 파일 원문이 작업에 없으면(예전 작업) 이 절은 붙지 않는다.
- 수정 찾기는 다른 파일 수정과 같다: 조각의 `first~last`를 그 파일의 허용 범위에 더한다.
- 캐시 키에는 이미 고칠 수 있는 파일의 sha256이 들어간다.

## 6. 지시문 (RTE)

두 처리 방식에 공통으로 더한다.

> Callers of the function may follow. Use them to see which values really reach its parameters. If every caller shown guarantees safety, answer no_fix and name the caller lines. If a caller passes an unsafe value and an editable caller can check it where the value is produced, you may fix it there and set "file" to that path.

- 가드 허용: 위에 더해, 호출자를 고치는 것이 위험하거나 호출자가 보이지 않는 곳에도 있을 수 있으면 이 자리에 가드를 넣는다.
- 근본 원인만: 원인이 보이지 않는 호출자에 있을 때만 manual이다.
- 보여 준 호출자가 전부가 아닐 수 있다는 점을 지시문에 적는다(요청 상한에 잘렸을 수 있다). 그래서 "수정 불필요"는 보여 준 호출자 모두가 안전하고 상한에 잘리지 않았을 때만 내게 한다. 잘렸으면 절 머리에 "N more call sites not shown"을 적는다.

## 7. 화면

- 바뀌는 것 없음. 호출자를 고치는 수정은 "다른 파일 수정" 칩이 붙고 자동 선택에서 빠진다(같은 파일 안 호출자는 칩 없이 검사 파일 수정으로 보인다).
- 작업 메모에 "호출 지점 N곳".

## 8. 시간과 크기

- `.c` 원문을 한 번 더 훑지 않도록 정의 찾기와 같은 반복에서 호출 지점도 찾는다.
- 저장하는 조각은 분석 대상 함수의 호출 지점뿐이다. 공용 함수는 호출 지점이 수백 곳일 수 있으므로 함수당 `MAX_STORED_CALLERS`(20)곳까지 저장한다(같은 파일 먼저, 그다음 파일 이름·줄 순서).

## 9. 테스트

- `enclosing_function`: 함수 안 줄, 함수 밖 줄, 여러 줄 서명
- `find_call_sites`: 직접 호출, 별칭 매크로를 통한 호출, 재귀 제외, 주석·문자열 안의 이름 제외, 겹치는 조각 합치기, 함수 범위로 자르기
- 저장 상한(함수당 20곳)과 순서
- 요청: 같은 파일 먼저, 4곳·120줄 상한, 잘리면 "more call sites not shown", 예전 작업은 절 없음
- 호출자 조각 안의 수정이 받아들여지고, 조각 밖은 다시 묻는지
- 웹: 프로젝트 폴더로 작업을 만들면 다른 파일의 호출 지점이 요청에 들어가고, 메모에 개수가 보이는지
- MISRA 요청에는 호출자 절이 없는지

테스트의 파일·함수 이름은 모두 지어낸 것이다.
