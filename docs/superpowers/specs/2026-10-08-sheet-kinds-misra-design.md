# Polyspace 시트 종류 기반과 MISRA 지원 설계

- 작성일: 2026-10-08
- 상태: 승인됨 (2026-10-08). 같은 날 정한 MISRA 조건 시트(3.5절)를 반영함
- 바탕: `2026-10-07-rte-fixer-design.md`(이하 RTE 설계). 이 문서에 적지 않은 것은 RTE 설계를 그대로 따른다.

## 1. 배경과 목표

`llm-fix-server`는 지금 Polyspace 결과 엑셀의 RTE 시트만 다룬다. 같은 엑셀에는 MISRA와 CodeMetrics 결과 시트도 있다. 이번 일의 목표는 두 가지다.

1. **시트 종류 기반:** 종류마다 다른 것(읽을 시트, 열, 분석할 행, 묶음, 지시문, 화면 표시)을 한곳에 모은다. 엑셀 읽기·LLM 호출·작업자·화면·DB는 종류를 몰라도 되는 공통 코드로 바꾼다. CodeMetrics는 다른 세션이 이 기반 위에 종류 하나로 더한다.
2. **MISRA 종류:** MISRA C:2012 시트를 RTE처럼 처리한다. 엑셀과 소스 폴더를 주면 행마다 "수정"(diff → 패치) 또는 "수정 불필요"(이유)가 나온다.

### 범위

포함:
- `kinds/` 패키지와 RTE·MISRA 종류
- 첫 화면에서 종류와 대상 고르기
- 묶음 처리: LLM 호출 하나가 행 여러 개를 맡고, 판단·이유는 행마다, 수정은 묶음이 함께 쓴다
- 공통 LLM 답 형식(RTE도 바뀜)
- 중요한 대상부터 처리, 작업 멈춤과 이어서 처리
- MISRA 조건 시트에서 규칙별 분류와 적용 여부 읽기
- DB 스키마 3, 화면, 테스트

제외(13절):
- CodeMetrics 종류(다른 세션)
- RTE·CodeMetrics 조건 시트
- 작업 하나에 여러 시트
- 규칙별로 골라 돌리기
- 엑셀에 결과 다시 쓰기

### 성공 기준

1. 첫 화면에서 MISRA와 대상 분류를 고르면, 그 행들이 묶음별로 처리되어 행마다 판단과 이유가 나온다.
2. RTE는 지금과 같은 흐름으로 동작한다. 바뀌는 것은 LLM 답 형식뿐이다.
3. 고른 수정을 패치 하나로 받아 `git apply`로 적용할 수 있다. 같은 묶음의 "수정" 행들은 같은 수정을 함께 쓰므로 서로 겹치지 않는다.
4. 패치를 적용하고 Polyspace를 다시 돌리면 "수정" 행의 위반이 사라지고, 다른 규칙 위반이나 새 Red/Orange가 생기지 않는다. 이 항목은 회사 PC에서 사람이 확인한다(12절).
5. "수정 불필요" 행의 이유는 Polyspace comment에 일탈(deviation) 사유로 그대로 옮길 수 있는 수준이다.
6. MISRA 규칙의 분류와 적용 여부는 작업마다 엑셀의 조건 시트에서 읽는다. 코드와 지시문에는 규칙별 값을 고정하지 않는다.

## 2. 사용자 결정 (2026-10-08)

| 항목 | 결정 |
|---|---|
| MISRA 처리 방식 | RTE처럼: 행마다 수정 / 수정 불필요(일탈 사유) |
| 일 나누기 | 이 일이 공통 기반과 MISRA를 먼저 만든다. CodeMetrics는 그 위에 더한다. |
| 작업 단위 | 작업 하나에 시트(종류) 하나. 첫 화면에서 고른다. |
| 묶음 | 종류별 묶음. 판단·이유는 행마다, 수정은 묶음 공유. RTE는 행 하나씩, MISRA는 같은 파일의 같은 줄. |
| MISRA 대상 기본값 | Mandatory·Required 체크, Advisory는 해제 |
| MISRA 분류와 적용 여부 | 조건 시트 우선. 작업마다 엑셀의 MISRA 조건 시트에서 읽고, 조건 시트에 없으면 결과 시트의 Category를 쓴다(3.5절). |
| 조건 값 | 바뀔 수 있으므로 코드와 지시문에 고정하지 않는다. |
| 처리량 | 중요한 대상부터 처리하고, 작업을 멈출 수 있게 한다. |
| Mandatory를 못 고칠 때 | `no_fix`로 답하되, 이유에 직접 고쳐야 한다는 것과 무엇이 부족한지 적는다. |
| 사내 정보 | 설계 문서·테스트·커밋에는 회사 값을 넣지 않는다(10절). |

## 3. 구조: 종류(kind)

### 3.1 파일 구성

```
src/llm_lab/
├─ kinds/
│  ├─ __init__.py   # Kind 정의, KINDS 목록, get_kind(), 공통 기본 함수
│  ├─ rte.py        # RTE 종류 (지금 fixer.py의 RTE 지시문을 옮긴다)
│  └─ misra.py      # MISRA 종류, 조건 시트 읽기
├─ polyspace.py     # read_sheet(엑셀, 종류, 고른 대상): 공통 시트·헤더 찾기
├─ fixer.py         # propose_fixes(종류, 묶음의 행들, 소스): 공통 답 형식
├─ worker.py        # 묶음 단위 처리, 중요한 대상부터
├─ store.py         # 스키마 3
├─ transcript.py    # 묶음마다 대화 기록 하나
├─ pages.py, web.py # 종류 고르기, 멈춤, 종류별 배지
```

- 새 종류는 `kinds/` 아래 파일 하나와 `KINDS` 목록 한 줄로 더한다. 공통 코드는 고치지 않아도 되게 한다.
- `polyspace`와 `fixer`는 `kinds`를 실행 중에 import하지 않는다(종류는 인자로 받는다). `kinds`가 `polyspace.Finding`과 `fixer.select_region`을 쓰기 때문이다.

### 3.2 `Kind`에 담는 것

`Kind`는 frozen dataclass다.

| 필드 | 뜻 |
|---|---|
| `name` | DB와 폼에 쓰는 이름: `rte`, `misra` |
| `title` | 화면 이름: `RTE`, `MISRA C:2012` |
| `sheet_word` | 시트 이름에 들어가는 낱말(소문자): `rte`, `misra` |
| `required`, `optional` | 필수 열, 선택 열(비교용 소문자) |
| `read_conditions(workbook)` | 엑셀 전체에서 이 종류의 조건을 읽어 (조건, 작업 메모에 남길 한 줄)을 돌려준다. 기본은 `(None, '')`. |
| `read_row(cell, row, conditions)` | 칸 읽기 함수, 엑셀 행 번호, 위의 조건을 받아, 대상이면 `Finding`을, 아니면 뺀 이유(문자열)를 돌려준다. |
| `label(finding)` | 배지 글자. 대상 고르기와 우선순위에도 쓴다. |
| `choices` | 첫 화면에서 고르는 대상. 중요한 것부터 적는다. |
| `defaults` | 처음에 체크된 대상 |
| `colors` | 배지 글자 → 배지 색(`red`, `orange`, `gray`, `blue`) |
| `group_key(finding, file_name)` | 함께 보낼 행의 기준. `''`이면 혼자 보낸다. |
| `system_prompt` | 시스템 메시지 |
| `heading` | 사용자 메시지 첫 줄 |
| `facts(finding)` | 사용자 메시지에 넣을 (이름, 값) 목록. 기본은 RTE와 같은 순서(6.2). |
| `region(text, findings)` | LLM에 보낼 줄 범위. 기본은 첫 행의 줄로 `select_region`(RTE 설계 6.1). |
| `prompt_version` | 지시문이나 답 형식을 바꾸면 올린다(캐시 키, 6.6). |

- 우선순위는 `choices` 안의 순서다. `choices`에 없는 배지 글자는 맨 뒤다.
- 대상 고르기는 `choices`에 있는 배지 글자에만 적용한다. `choices`에 없는 글자의 행은 늘 남는다.
- CodeMetrics처럼 고를 대상이 없는 종류는 `choices`를 비워 둔다. 그러면 체크 상자가 나오지 않는다.

### 3.3 RTE 종류 (`kinds/rte.py`)

| 항목 | 값 |
|---|---|
| 필수 열 | `type`, `file`, `line`, `check`, `detail` (지금과 같음) |
| 선택 열 | `id`, `group`, `information`, `function`, `status`, `comment` |
| 분석할 행 | 공백을 정리하고 대소문자를 무시한 TYPE이 `red check`이면 Red, `orange check`이면 Orange(지금과 같음). 그 밖의 값은 TYPE 글자(빈 칸이면 `(빈 TYPE)`)를 이유로 뺀다. |
| 배지·대상 | `Finding.color`: Red(빨강), Orange(주황). 둘 다 기본 체크 |
| 묶음 | 없음(`''`, 행 하나씩) |
| 지시문 | 지금 RTE 지시문. 마지막 줄의 답 형식 설명만 바꾼다(6.5). |
| `heading` | `Polyspace Code Prover result` |
| `prompt_version` | 4 (지금 `PROMPT_VERSION` 3에서 올림) |

### 3.4 MISRA 종류 (`kinds/misra.py`)

MISRA 시트의 열은 RTE와 같고 `ID`만 없다: `TYPE`, `Group`, `information`, `File`, `Function`, `Line`, `Check`, `Detail`, `Status`, `Comment`. 필수·선택 열은 RTE와 같다. `ID`는 원래 선택 열이다.

| 항목 | 값 |
|---|---|
| 분석할 행 | 모든 행. TYPE에는 규칙 표준 이름(`MISRA C:2012`)이 들어 있어 거르지 않는다. 다만 조건 시트에서 꺼진 규칙의 행은 "적용 안 함 (조건 시트)"로 뺀다(3.5). |
| 배지·대상 | 실제 분류 `Finding.category`(3.5): `Mandatory`(빨강), `Required`(주황), `Advisory`(회색). 그 밖의 분류와 `분류 없음`은 회색이다. |
| 기본 체크 | Mandatory, Required |
| 묶음 | 같은 파일의 같은 줄: `f"{file_name}:{line}"`. 줄 번호가 정수가 아니면 혼자(`''`). |
| `facts` | `TYPE`, `check`, `detail`, `Group`, `category`, `Function`, `File`, `line`. `information` 대신 실제 분류를 보낸다. 두 값이 다를 때 LLM이 헷갈리지 않게 하기 위해서다. |
| 지시문 | 6.5의 MISRA 지시문 |
| `heading` | `Polyspace MISRA C:2012 results` |
| `prompt_version` | 1 |

- `Check`에는 규칙 번호와 규칙 문장이 함께 있다. 그래서 규칙 설명을 따로 올리는 기능은 만들지 않는다.
- MISRA 행에서 `Finding.color`는 `''`다. 나머지 칸은 지금 `Finding` 필드에 그대로 담긴다(`type`, `group`, `information`, `check`, `detail`, `function`, `status`, `comment`). 새 필드 `category`, `note`는 4절에 적는다.

### 3.5 MISRA 조건 시트 (`kinds/misra.py`의 `read_conditions`)

엑셀에는 결과 시트와 별도로 규칙마다 프로젝트 분류(Mode)와 적용 여부(Enabled)를 적은 조건 시트가 있다. 사용자 결정은 "조건 시트 우선"이다. 내용은 바뀔 수 있으므로 작업마다 엑셀에서 읽는다.

**찾기**
- 이름이 `_Result`로 끝나지 않는 시트 중에서, 위 30행 안에 `guideline`과 `mode` 열이 있는 헤더 행을 가진 시트를 찾는다. 시트 이름은 고정하지 않는다.
- 그런 시트가 여럿이면 이름에 `misra`가 들어간 시트를 쓰고, 그래도 여럿이면 첫 시트를 쓴다.
- 열 이름은 대소문자, 공백, 끝의 콜론(`:`)을 무시하고 비교한다. 이 비교는 결과 시트 헤더에도 똑같이 쓴다.
- 필수 열은 `guideline`, `mode`, 선택 열은 `enabled`다. 다른 열(설명, 코멘트, 검토 범위 등)은 쓰지 않는다.

**규칙 번호 맞추기 (`rule_key`)**
- 글 맨 앞의 규칙 번호(`10.3`처럼 점으로 이은 숫자)를 뽑는다. 앞에 `Rule`이 붙어도 된다. 결과 시트는 `Check`에서, 조건 시트는 `Guideline`에서 뽑는다.
- `Dir`, `Directive`, `D`가 붙었거나 결과 행의 `Group`에 `directive`가 들어 있으면 디렉티브로 보고 `D`를 앞에 붙인다. Rule 4.1과 Dir 4.1이 섞이지 않게 하기 위해서다.
- 번호를 뽑지 못한 결과 행은 조건 시트를 쓰지 않는다.

**값 읽기**
- Mode는 공백을 정리하고 대소문자를 무시한다. mandatory, required, advisory는 `Mandatory`, `Required`, `Advisory`로 쓴다. 그 밖의 값은 첫 글자만 대문자로 바꿔 그대로 쓴다. 이런 값은 `choices`에 없으므로 그 행은 늘 남는다. Mode가 빈 규칙은 조건 시트에 없는 것으로 본다.
- Enabled도 공백을 정리하고 대소문자를 무시한다. `no`, `n`, `off`, `false`, `0`, `disabled`이면 꺼진 규칙이다. 그 밖의 값, 빈 칸, Enabled 열이 없을 때는 켜진 것으로 본다.

**분류 정하기 (`Finding.category`)**
1. 조건 시트에 그 규칙이 있으면 그 Mode를 쓴다.
2. 없으면 결과 시트 `information`에서 분류 낱말(mandatory/required/advisory, 대소문자 무시)을 찾아 쓴다. 칸 형식은 `Category: Required`다.
3. 그것도 없으면 `분류 없음`이다.
- 1과 2가 둘 다 있고 다르면 `Finding.note`에 `분류: 조건 시트 Advisory (결과 시트 Required)` 형식으로 남긴다. 결과 카드에 보인다.

**작업 메모**
- 조건 시트를 읽었으면 `조건 시트: 규칙 N개`를, 못 찾았거나 읽을 규칙이 없으면 `조건 시트 없음: 결과 시트 분류를 씀`을 작업 메모(7.1)에 남긴다.

**고정하지 않는 것**
- 규칙 번호, 규칙별 분류와 적용 여부, 시트 이름은 모두 작업마다 엑셀에서 읽는다.
- 코드에 고정된 것은 MISRA 표준 분류 이름 세 개(대상 체크 상자와 우선순위)와, 꺼짐을 뜻하는 낱말 목록뿐이다.

## 4. 엑셀 읽기 (`polyspace.read_sheet`)

`read_sheet(source, kind, selected) -> Sheet`가 지금의 `read_rte`를 대신한다.

**시트 고르기**
- 이름이 `_Result`로 끝나고 `kind.sheet_word`가 들어간 시트만 본다. 대소문자는 무시한다.
- 그중 위에서 30행 안에 `kind.required` 열이 모두 있는 헤더 행을 가진 첫 시트를 쓴다(헤더 찾기는 RTE 설계 4절과 같다).
- MISRA와 RTE 시트는 열이 같으므로, 이름으로 고르는 것이 유일한 구분이다. 지금처럼 "이름이 맞지 않아도 첫 시트를 쓰는" 대체 규칙은 없앤다.
- 못 찾으면 `SheetError`를 낸다. 예: "MISRA C:2012 시트를 찾지 못했습니다. 이름에 'misra'가 들어가고 _Result로 끝나는 시트가 필요합니다." 지금처럼 시트 이름과 각 시트에서 찾은 헤더를 함께 담는다. 이 오류는 회사 PC의 화면에만 나온다.

**조건 읽기**
- 시트를 고르기 전에 `kind.read_conditions(workbook)`으로 조건과 작업 메모 한 줄을 얻는다. MISRA는 3.5절, RTE는 조건이 없다.

**행 읽기**
- 모든 칸이 빈 행은 세지 않고 건너뛴다(지금과 같음).
- `kind.read_row(cell, row, conditions)`가 이유를 돌려주면 그 이유로 센다.
- `Finding`이면 `kind.label`을 구한다. 그 글자가 `kind.choices`에 있는데 `selected`에 없으면 `"{글자} (고르지 않음)"` 이유로 센다.

**결과**

```python
@dataclass(frozen=True)
class Sheet:  # 지금의 RteSheet
    sheet: str
    findings: list[Finding]
    skipped: dict[str, int]  # 뺀 이유 → 행 수 (처음 나온 순서)
    conditions_note: str = ""  # read_conditions가 준 작업 메모 한 줄
```

`Finding`에는 기본값이 있는 필드 두 개를 더한다. 기본값이 있으므로 DB에 저장된 예전 행 JSON도 그대로 읽힌다. 나중에 다른 종류가 필드를 더할 때도 같은 규칙을 따른다.

| 필드 | 뜻 |
|---|---|
| `category: str = ""` | MISRA의 실제 분류(3.5). 다른 종류는 비워 둔다. |
| `note: str = ""` | 결과 카드에 보일 짧은 메모. MISRA는 분류가 다를 때 쓴다. CodeMetrics는 "Actual 19 > 기준 15" 같은 값을 쓸 수 있다. |

## 5. 묶음과 처리 순서

- 작업을 만들 때 소스를 찾은 행마다 `kind.group_key(finding, file_name)`을 구해 `rows.grp`에 저장한다. 소스가 없는 행은 `''`이다.
- 같은 작업에서 `grp`가 같은 `pending` 행들이 묶음 하나다. `grp`가 `''`인 행은 혼자 묶음이다.
- 묶음 안의 행 순서는 (줄, check, detail, 엑셀 행)으로 정한다. 줄 번호가 없으면 0으로 본다. 이 순서가 LLM 메시지의 번호(Finding 1, 2…)이고 캐시 결과의 순서다. 엑셀 행은 마지막 기준이라, 엑셀을 다시 뽑아 행 번호가 바뀌어도 순서가 거의 그대로다.
- 묶음을 작업자에 넣는 순서: (묶음 안에서 가장 중요한 대상의 순위, 가장 작은 엑셀 행). 그래서 MISRA는 Mandatory, Required, Advisory 순으로 처리된다. 멈췄을 때 중요한 것부터 결과가 남는다.

## 6. LLM 요청과 답 (`fixer.py`)

`propose_fixes(client, model, kind, findings, source, transcript=None) -> list[FixResult]`가 지금의 `propose_fix`를 대신한다. 돌려주는 목록은 `findings`와 같은 순서다.

### 6.1 코드 범위
- 먼저 모든 행의 줄 번호를 검사한다. 정수가 아니거나 파일 길이를 넘으면, 지금과 같은 오류("엑셀의 줄 번호가 소스와 맞지 않습니다")로 묶음 전체가 실패한다.
- 범위는 `kind.region(text, findings)`로 정한다. RTE와 MISRA는 기본 함수(첫 행의 줄로 `select_region`)를 쓴다. MISRA 묶음은 모두 같은 줄이다.
- 수정 위치 찾기에서 같은 코드가 여러 번 나오면 첫 행의 줄로 고른다(RTE 설계 6.4의 5).

### 6.2 사용자 메시지

```
{kind.heading}:

Finding 1:
- TYPE: …
- check: …
- detail: …
- Group: …
- information: …
- Function: …
- File: {source.name}
- line: …

Finding 2:
…

Code (lines {first}-{last} of {source.name}; each line starts with its number and '| '):
```c
{줄 번호 붙은 코드}
```
```

- 기본 `facts`는 지금 RTE와 같은 순서이고, 빈 값은 뺀다. 위 모양은 RTE 기준이다.
- MISRA는 `information` 줄 대신 `- category: …`(실제 분류) 줄을 넣는다(3.4).
- RTE도 묶음이 하나뿐이지만 같은 모양(`Finding 1:`)을 쓴다.

### 6.3 공통 답 형식 (`json_schema`, strict)

```json
{
  "findings": [{ "number": 1, "decision": "fix | no_fix", "reason": "string" }],
  "edits": [{ "original": "string", "replacement": "string" }]
}
```

- `findings`에는 메시지의 번호마다 판단과 이유가 하나씩 있다.
- `edits`는 묶음 전체에 한 벌이다. `fix` 행에만 붙는다.
- 스키마 이름은 `polyspace_fix`다.

### 6.4 답 검사와 행별 결과

다음 중 하나면 지금처럼 문제를 알려 주고 한 번 다시 묻는다(RTE 설계 6.5). 두 번째도 실패하면 묶음 전체가 오류다.

| 문제 | 오류 종류(화면 설명) |
|---|---|
| `finish_reason`이 `length` | 잘림 |
| JSON이 아니거나, `findings` 항목이 스키마와 다름 | 형식 |
| 번호가 1~n을 한 번씩 담지 않음(빠짐, 겹침, 범위 밖) | 형식 |
| `fix`가 있는데 `edits`가 비었거나 형식이 다름 | 형식 |
| 소스 인코딩으로 쓸 수 없는 문자 | 인코딩 |
| `original`을 찾지 못함, 여러 번 나옴, edit끼리 겹침, 적용해도 같음 | 위치 |

- 모두 `no_fix`면 `edits`는 버린다(지금 RTE와 같음).
- 행별 결과:
  - `fix` 행: `FixResult("fix", 이유, 묶음 edits, 묶음 diff)`
  - `no_fix` 행: `FixResult("no_fix", 이유, (), "")`
- 같은 묶음의 `fix` 행들은 위치와 내용까지 같은 edit를 가진다. 패치는 똑같은 수정을 하나로 합치고 충돌로 보지 않는다(RTE 설계 9절). 그래서 패치와 충돌 코드는 바꾸지 않는다.

### 6.5 지시문

**RTE:** 지금 지시문의 마지막 줄 `Answer with JSON only. For no_fix, "edits" is [].`를 아래로 바꾼다. 나머지는 그대로다.

```
Answer with JSON only. "findings" has one entry for each numbered finding. "edits" is [] when no finding is fix.
```

**MISRA (전문):**

```
You review Polyspace results for MISRA C:2012 in C code. All findings in one request are on the same line. For each finding, decide whether the code must change.
- "check" gives the rule number and its headline, "detail" what Polyspace found, and "category" the rule category this project uses.
- Mandatory: no deviation is allowed, so fix it. If the code shown is not enough to fix it, answer no_fix and say in reason that it must be fixed by hand and what is missing.
- Required and Advisory: answer fix when a small change removes the violation without changing behavior. Otherwise answer no_fix with a deviation rationale.
When you fix:
- Keep the behavior the same for every input. Typical fixes are explicit casts to the intended essential type, U suffixes on unsigned constants, added parentheses or braces, and splitting a statement.
- Use the type names the code already uses, such as its typedefs.
- Do not add new MISRA C:2012 violations. For example, do not cast a composite expression (Rule 10.8); cast its operands.
- One set of edits is shared by every finding you answer fix, so fix them together.
- Change as little as possible. Do not touch lines unrelated to the findings.
- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, without the line-number prefix. "replacement" is the new text for those lines.
- Keep each edit small: "original" holds only the lines you change. Add one unchanged neighbouring line only when the changed line appears more than once.
Do not guess definitions (macros, types, other functions) that are not shown. If they matter, say so in reason.
Write each "reason" in Korean, one to three sentences that a reviewer can paste into the Polyspace comment of that finding. For fix, say what changed. For no_fix, write why the code is acceptable as written.
Answer with JSON only. "findings" has one entry for each numbered finding. "edits" is [] when no finding is fix.
```

### 6.6 재사용(캐시)

- 키는 다음을 이어 붙인 문자열의 sha256이다: `kind.name`, `kind.prompt_version`, 모델명, 원본 파일 sha256, 그리고 묶음의 행마다(5절 순서) `kind.facts`의 값들.
- 값은 행 순서대로 늘어놓은 `FixResult` JSON 목록이다.
- 키에 종류와 버전이 들어가므로 지금 캐시(RTE 단일 결과)는 다시 쓰이지 않는다. RTE도 한 번은 LLM을 다시 부른다. 저장된 값이 목록이 아니면 없는 것으로 본다.

## 7. 작업 처리와 저장

### 7.1 DB 스키마 3 (`store.py`)

| 표 | 더하는 칸 | 예전 DB의 값 |
|---|---|---|
| `jobs` | `kind TEXT NOT NULL DEFAULT 'rte'` | `rte` (예전 작업은 모두 RTE) |
| `jobs` | `note TEXT NOT NULL DEFAULT ''` | `''` |
| `rows` | `grp TEXT NOT NULL DEFAULT ''` | `''` (행 하나씩) |

- `SCHEMA_VERSION`을 3으로 올린다. 켤 때 없는 칸은 `ALTER TABLE … ADD COLUMN`으로 더한다(스키마 2의 `source_root`와 같은 방식).
- `note`는 작업 메모 한 줄이다. 조건 메모(`Sheet.conditions_note`)와 뺀 이유별 개수를 ` · `로 잇는다. 예: `조건 시트: 규칙 12개 · 제외: Advisory (고르지 않음) 40, 적용 안 함 (조건 시트) 2`. `skipped`(합계)는 그대로 둔다.
- `Job`에 `kind`, `note`, `Row`·`NewRow`에 `grp`를 더한다(기본값 있음).
- `claim_rows(ids)`: 모든 행이 `pending`일 때만 한꺼번에 `running`으로 바꾸고 `True`를 돌려준다. 하나라도 아니면 아무것도 바꾸지 않고 `False`다. 쓰기 잠금 안에서 한 번에 한다.
- `stop(job_id)`: `running` 작업만 `stopped`로 바꾸고 메시지에 `USER_STOP`(사용자가 멈춤)을 적는다.
- `cache_get`·`cache_put`은 `FixResult` 목록을 다룬다.

### 7.2 작업자: 묶음 단위 (`worker.py`)

- `start(job_id)`: `pending` 행을 묶음으로 모아 5절 순서로 묶음마다 작업 하나를 넣는다.
- 묶음 처리:
  1. 작업이 `running`이고 `claim_rows`가 성공할 때만 처리한다. 다시 시도를 여러 번 눌러 같은 묶음이 두 번 들어가도 한 번만 처리된다.
  2. 행을 5절 순서로 정렬하고, 캐시를 보고, 없으면 `propose_fixes`를 부른다.
  3. 행마다 자기 결과를 `done`으로 저장한다.
- 치명 오류(네트워크·SSL·인증): 묶음의 행을 `pending`으로 되돌리고 작업을 `stopped`로 바꾼다(지금과 같음).
- 그 밖의 오류: 묶음의 모든 행을 같은 메시지로 `error`로 바꾼다.
- 서비스를 켤 때 복구 규칙은 RTE 설계 7절과 같다. 묶음은 `pending` 행에서 다시 만든다.

### 7.3 멈춤과 이어서 처리

- `POST /results/{id}/stop`: `store.stop`. 이미 줄 서 있던 묶음은 작업이 `running`이 아니어서 건너뛴다. LLM을 기다리던 묶음은 답이 오면 결과를 저장한다.
- 멈춘 작업은 "끝난" 작업으로 본다. 그래서 그때까지 처리된 행으로 패치를 받을 수 있다.
- 이어서 처리는 지금의 `POST /results/{id}/retry`를 그대로 쓴다. 오류 행을 `pending`으로 되돌리고, 작업을 `running`으로 바꾸고, `pending` 묶음을 다시 넣는다.

### 7.4 대화 기록 (`transcript.py`)

- 묶음마다 파일 하나: `시각_job{작업}_row{묶음의 가장 작은 엑셀 행}.md`
- 맨 위에 종류와 묶음의 행들(엑셀 행, check, detail, 위치)을 적는다. 결과는 행마다 한 줄이다("행 12: 수정 - 이유"). 오류면 한 줄이다.
- 그 밖에는 RTE 설계 7절과 같다.

## 8. 화면과 API

**공통**
- 서비스 이름: "Polyspace 수정 제안"
- 배지 글자와 색은 `kind.label`과 `kind.colors`를 따른다. 결과 카드 왼쪽 띠도 같은 색이다.

**첫 화면 (`GET /`)**
- 단계: ① 엑셀 ② 소스 폴더 ③ **분석할 시트** ④ 분석 시작
- ③은 종류마다 라디오 버튼 하나(`name="kind"`, 처음엔 RTE)와 그 종류의 대상 체크 상자(`name="choice"`, 값은 `종류:대상`, 예: `misra:Required`)다. 페이지 안의 작은 스크립트가 고른 종류의 체크 상자만 보여 준다.

**작업 만들기 (`POST /results`)**
- 새 필드: `kind`(없으면 `rte`), `choice`(여러 개)
- 알 수 없는 `kind`면 `400`이다.
- `choice` 중 `{kind}:`로 시작하는 것만 고른 대상으로 쓴다. `kind.choices`에 없는 값은 무시한다. `kind.choices`가 있는데 하나도 고르지 않았으면 `400`: "분석할 대상을 하나 이상 고르세요."
- `read_sheet` → 소스 찾기 → `group_key`로 `grp`를 붙여 저장한다.

**작업 목록**
- "Red / Orange" 칸을 "종류 · 대상"으로 바꾼다. 종류 이름과 대상별 개수 배지(`choices` 순서, 그 밖의 글자는 뒤)를 보여 준다.

**결과 화면 (`GET /results/{id}`)**
- 머리말: 종류 · 시트 · 시각 · 소스 폴더. 그 아래 줄에 작업 메모(`note`)가 있으면 보여 준다.
- 처리 중: 진행 막대 옆에 "멈춤" 버튼(`POST /results/{id}/stop`)
- 멈춘 작업:
  - 사용자가 멈췄으면 회색 안내 "멈춘 작업입니다. '이어서 처리'를 누르면 남은 행을 처리합니다."
  - 치명 오류로 멈췄으면 지금처럼 빨간 상자에 원인을 보여 준다.
  - 버튼은 둘 다 "이어서 처리"다.
- 끝났는데 오류 행이 있으면 지금처럼 "오류 행 다시 시도" 버튼을 보여 준다.
- 개수 한 줄: 대상별 개수(`choices` 순서), 제외 | 수정, 수정 불필요, 확인 필요, 대기
- "한눈에 보기" 표: 종류 칸에 배지. check 칸은 한 줄로 자르고(`text-overflow: ellipsis`), 마우스를 올리면 전체가 보인다. MISRA의 긴 규칙 문장 때문이다.
- "자세히 보기" 카드:
  - `Finding.note`가 있으면 detail 줄 아래에 보여 준다. 한눈에 보기 표의 배지에도 마우스를 올리면 보인다.
  - 묶음으로 보낸 행이면 "함께 보낸 행: 엑셀 13, 14행"을 보여 준다.
  - 대화 기록 파일 이름에는 묶음의 가장 작은 엑셀 행을 쓴다.
- "수정 모두 선택"의 순서: (우선순위, 엑셀 행). RTE는 지금처럼 Red 먼저다.
- 행이 없으면 "분석할 행이 없습니다".

**API (`GET /api/results/{id}`)**
- 작업에 `kind`, `note`가 더해진다(`asdict(job)`). 행마다 `grp`를 더한다. `finding`에는 `category`, `note`가 들어 있다.

## 9. 오류 처리

RTE 설계 10절에 다음을 더하거나 바꾼다.

| 상황 | 처리 |
|---|---|
| 고른 종류의 시트나 필수 열이 없음 | 업로드 직후 `400`. 종류 이름, 필요한 시트 이름 조건, 시트 이름과 찾은 헤더를 보여 준다. |
| 알 수 없는 종류 | `400` |
| 대상을 하나도 고르지 않음 | `400` "분석할 대상을 하나 이상 고르세요." |
| 고른 대상의 행이 없음 | 작업은 만들되 바로 `done`. "분석할 행이 없습니다" |
| MISRA 조건 시트가 없거나 읽을 규칙이 없음 | 결과 시트 분류를 쓰고 작업 메모에 "조건 시트 없음: 결과 시트 분류를 씀"을 남긴다. 오류는 아니다. |
| 조건 시트에서 꺼진 규칙 | 그 행은 "적용 안 함 (조건 시트)"로 뺀다. |
| 조건 시트와 결과 시트의 분류가 다름 | 조건 시트를 따르고, 카드에 두 값을 보여 준다(`Finding.note`). |
| 묶음의 줄 번호 오류, LLM 답 오류 | 묶음의 모든 행이 같은 오류(확인 필요) |
| 치명 오류 | 묶음의 행은 `pending`, 작업은 `stopped`(지금과 같음) |
| 사용자가 멈춤 | 작업 `stopped`, 메시지 `USER_STOP`. "이어서 처리"로 계속한다. |

## 10. 사내 정보 규칙

전역 규칙(사용자 CLAUDE.md, "회사 내부 정보는 절대 회사 밖으로 내보내지 않는다")을 이 일에 이렇게 적용한다.

- 설계 문서, 구현 계획, 테스트, 커밋 메시지에는 회사 엑셀의 값(시트 이름, 행 수, 파일·함수 이름, 칸 원문, 규칙별 분류와 적용 여부)을 넣지 않는다. 테스트의 엑셀과 소스는 만들어 낸 것만 쓴다.
- 테스트의 조건 시트는 실제와 상관없는 가짜 값을 섞어 쓴다: 대소문자가 섞인 Mode, 끝에 콜론이 붙은 헤더, 꺼진 규칙, 결과 시트와 다른 분류, 다른 이름의 시트.
- 결과 화면, 오류 화면, 대화 기록은 회사 PC 안에서만 본다. 그래서 지금처럼 파일 이름과 원문을 보여 준다.
- todo.md의 회사 PC 확인 절은 이 화면들의 캡처를 부탁하지 않는다. "알려줄 것"은 개수와 종류를 말로만 받는다. 이상했던 판단은 코드와 이름 없이 말로 설명해 달라고 적는다.

## 11. 테스트

모두 회사 LLM 없이 돌아간다(RTE 설계 11절의 가짜 LLM). 지금 테스트는 새 답 형식과 새 함수 이름에 맞춰 고쳐서 모두 통과시킨다.

- `test_kinds.py` (새 파일)
  - MISRA 분류 읽기: `Category: Required` → Required, 대소문자 차이, 낱말 없음 → 분류 없음
  - `rule_key`: `10.3 …` → `10.3`, `Rule 10.3` → `10.3`, `Dir 4.1`·`D4.1` → `D4.1`, Group에 directive → `D` 접두, 맨 앞에 번호가 없으면 없음(규칙 문장 속 숫자는 뽑지 않음)
  - 조건 시트 찾기: 이름이 아니라 열로 찾는다, 끝 콜론 헤더, 여럿이면 이름에 misra가 든 시트, `_Result` 시트는 보지 않는다
  - 조건 값: Mode 대소문자, 빈 Mode는 없는 규칙, Enabled 꺼짐 낱말들과 그 밖의 값, Enabled 열 없음
  - 분류 정하기: 조건 시트 우선, 없으면 결과 시트, 다르면 `note`, 꺼진 규칙은 "적용 안 함 (조건 시트)"
  - MISRA 묶음 키: 같은 파일·같은 줄은 같은 키, 줄이 다르거나 파일이 다르면 다른 키, 줄 번호 없음 → `''`
  - RTE `read_row`: Red/Orange는 `Finding`, Gray와 빈 TYPE은 이유
  - `KINDS` 이름이 겹치지 않고, `defaults`가 `choices` 안에 있는지
- `test_polyspace.py`
  - 열이 같은 RTE·MISRA 시트가 한 엑셀에 있을 때 종류에 맞는 시트를 고른다.
  - 이름이 맞는 시트가 없으면 종류 이름이 든 오류
  - ID 열이 없는 MISRA 헤더
  - 고르지 않은 대상과 Gray를 이유별로 센다.
  - 조건 시트가 있는 엑셀: 조건 시트 분류로 대상을 고른다, 꺼진 규칙을 뺀다, `conditions_note`에 규칙 수
  - 조건 시트가 없는 엑셀: 결과 시트 분류를 쓰고 "조건 시트 없음" 메모
- `test_fixer.py`
  - 묶음 메시지: 번호 붙은 Finding 목록, RTE·MISRA 지시문
  - 행별 판단: `fix`·`no_fix`가 섞인 답에서 수정이 `fix` 행에만 붙는다.
  - 모두 `no_fix`인데 `edits`가 있으면 버린다.
  - 번호 빠짐·겹침·범위 밖 → 다시 묻기, 두 번 실패 → 형식 오류
  - 묶음의 줄 번호 오류
  - 캐시 키: 종류, 버전, 행이 다르면 달라진다.
- `test_worker.py`
  - 같은 줄 MISRA 두 행이면 LLM 호출이 한 번이고, 두 행 모두 결과를 받는다.
  - 우선순위 순서로 묶음을 넣는다(Mandatory가 Advisory보다 먼저).
  - 멈춘 작업의 남은 묶음은 처리하지 않고, 이어서 처리하면 처리한다.
  - `claim_rows`가 실패하면 처리하지 않는다.
- `test_store.py`
  - 스키마 2 DB를 3으로 옮긴다: 예전 작업 `kind='rte'`, 예전 행 `grp=''`
  - `claim_rows`는 전부 아니면 하나도 바꾸지 않는다.
  - `stop`은 `running` 작업만 바꾼다.
  - 캐시 목록 저장과 읽기, 목록이 아닌 예전 값은 없는 것으로 본다.
- `test_web.py`
  - 첫 화면에 종류 라디오와 대상 체크 상자가 있다.
  - `kind=misra`와 대상으로 작업을 만들면 MISRA 배지, 작업 메모(조건 시트, 제외 이유), 분류가 다른 행의 메모가 보인다.
  - 알 수 없는 종류, 대상 없음 → `400`
  - 멈춤 경로, 멈춘 작업의 "이어서 처리"
  - 같은 묶음의 `fix` 행 두 개를 함께 골라도 패치가 만들어진다(같은 수정 합치기).
- `test_transcript.py`: 묶음 기록의 파일 이름과 행별 결과 줄

## 12. 회사 PC 확인

구현이 master에 올라가면 todo.md에 절 하나를 더한다(10절 규칙을 따른다).

1. `git pull` 뒤 `uv run llm-fix-server`를 켠다.
2. 첫 화면에서 MISRA를 고르고 Mandatory만 체크해 작게 분석한다.
3. 결과를 훑어보고 패치를 받아 적용한 뒤 Polyspace를 다시 돌린다.
4. 알려줄 것: 수정 / 수정 불필요 / 확인 필요 개수, 작업 메모에 조건 시트가 읽혔다고 나왔는지와 분류가 다르다는 메모가 붙은 행 수, 함께 보낸 묶음이 있었는지, 패치 적용이 됐는지(안 됐다면 오류 종류), Polyspace 재실행 뒤 "수정" 행의 위반이 없어졌는지와 새 위반 수, 이상했던 판단(코드·이름 없이 말로)

## 13. 나중에 (이번에 하지 않는 것)

- **CodeMetrics 종류:** 다른 세션이 `kinds/codemetrics.py`로 더한다. 필요한 것은 이 기반에 자리가 있다: 종류별 필수 열, `read_row`, 함수 단위 `group_key`, 함수 전체를 보내는 `region`(너무 길면 오류), 지시문, `facts`(Threshold·Actual Value), 대상 없는 `choices`. `Finding`에 필드를 더할 때는 기본값을 둔다.
- **RTE·CodeMetrics 조건 시트:** 다른 세션이 내용을 확인하고 있다. 반영할 때는 그 종류의 `read_conditions`를 채우고 `read_row`에서 쓴다(MISRA와 같은 방식).
- **MISRA 조건 시트의 나머지 열:** 검토 범위(Review Scope)와 Comment 열은 뜻을 알게 되면 반영한다. 지금은 쓰지 않는다.
- 작업 하나에 여러 시트, 규칙별로 골라 돌리기, 엑셀 `status`·`comment`에 결과 다시 쓰기
- Mandatory인데 `no_fix`인 행을 따로 눈에 띄게 표시하기
