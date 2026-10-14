"""회사 밖으로 보낼 화면에 찍어도 되는 글인지 가린다.

숫자와 공개된 도구·표준 용어(VOCAB)로만 된 글은 그대로 두고, 나머지는 길이만 남긴다.
살펴보기 도구(scripts/peek_*.py)와 서버의 요약 페이지가 함께 쓴다.
"""

from __future__ import annotations

import re

MAX_DIGITS = 5  # 이보다 긴 숫자는 사번 같은 번호일 수 있어 자릿수만 찍는다

# 그대로 찍어도 되는 낱말. 공개된 도구·표준 용어와 흔한 표 머리 낱말만 둔다.
# 이 낱말과 숫자만으로 된 칸은 회사 이름을 담을 수 없다.
VOCAB = frozenset(
    """
    misra c rule rules dir directive directives category mandatory required advisory decidable
    undecidable decidability single translation unit system analysis scope amendment amd addendum
    guideline guidelines deviation deviations permitted disapplied readopted compliance compliant
    violation violations headline title description section chapter version standard
    the a an of and or in on to by for with within without not non no yes
    environment unused comments character sets lexical conventions identifiers types literals
    constants declarations definitions initialization essential type model pointer pointers
    conversions expressions side effects control statement statements flow switch functions arrays
    overlapping storage preprocessing libraries library resources implementation code
    comment density cyclomatic complexity estimated function coupling language number call calls
    levels level occurrences called calling executable lines line parameters parameter goto
    instructions instruction body local static variables variable paths path return higher lower
    estimate size direct recursions recursion files file header program maximum minimum stack usage
    protected potentially unprotected shared global project his comf stmt param vocf nomv nomvpr
    ap cg cycle
    absolute address correctness condition division zero reachable illegally dereferenced invalid
    specific operations shift use routine initialized terminating loop null this method out bounds
    array index overflow value subnormal float unreachable user assertion uncaught exception
    incorrect object oriented programming deallocation previously deallocated memory obai zdv idp
    niv nivl nip ovfl unr shf cor fnc fnr ntc ntl std lib asrt
    red orange green gray grey check checks checked unchecked unreviewed investigate fix justified
    action planned defect other true false enabled disabled enable disable applied apply unapplied
    na pass fail ok ng none all total count high medium low severity
    name value values min max limit limits threshold thresholds metric metrics actual status
    group family information detail details color colour acronym note notes remark remarks result
    results target targets criteria criterion conditions upper item items kind class
    classification priority review reviewed justification rationale reason setting settings option
    options mode default id cover overview summary list polyspace bug finder prover rte
    규칙 분류 설명 기준 기준치 기준값 지표 값 상태 비고 적용 미적용 여부 대상 비대상
    제외 판정 조건 검사 항목 번호 이름 최소 최대 이상 이하 초과 미만 결과 개수 건 개
    예 아니오 사용 미사용 필수 권고 권장 선택 허용 불가 금지 없음 있음 검토 수정 확인
    필요 불필요 위반 준수 미준수 종류 등급 레드 오렌지 그린 회색 체크 측정 함수 파일
    단위 범위 내용 구분 의무 강제 참고 적합 부적합 버전 약어 분석 방법 방식 메트릭 명 명칭 유형
    그룹 대분류 중분류 소분류 카테고리 경고 주의 통과 실패 합격 불합격
    data warning caution nesting depth
    """.split()
)
SPLIT = re.compile(r"[\s,;:/\\()\[\]{}|=<>≤≥+\-*'\"._%#~!?&]+")


def is_number(value: str) -> bool:
    try:
        float(value.replace(",", ""))
    except ValueError:
        return False
    return True


def allowed(word: str) -> bool:
    lower = word.casefold()
    return (
        lower in VOCAB
        or (word.isdecimal() and len(word) <= MAX_DIGITS)
        or (len(word) == 1 and word.isascii() and word.isalpha())
        or re.fullmatch(r"r20\d\d[ab]", lower) is not None  # Polyspace 릴리스 (R2023b)
        or re.fullmatch(rf"\d{{1,{MAX_DIGITS}}}(건|개|줄|행|자|회|번)", word) is not None
    )


def safe(value: str) -> str:
    """화면에 찍어도 되는 모양. 숫자와 VOCAB 낱말로만 된 글은 그대로, 나머지는 길이만."""
    if not value:
        return ""
    if is_number(value):
        digits = sum(ch.isdigit() for ch in value.split(".")[0])
        return value if digits <= MAX_DIGITS else f"<숫자 {digits}자리>"
    words = [word for word in SPLIT.split(value) if word]
    if words and all(allowed(word) for word in words):
        return value
    return f"<글 {len(value)}자>"
