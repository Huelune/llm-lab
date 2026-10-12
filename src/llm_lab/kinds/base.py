"""시트 종류(Kind): 종류마다 다른 것(읽을 시트·열, 분석할 행, 배지, LLM 지시와 답)을 모은 정의."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_lab.fixer import (
    FIX_SCHEMA,
    FIX_TIMEOUT,
    FixResult,
    Request,
    edits_results,
    select_region,
)
from llm_lab.polyspace import Finding, Prepared
from llm_lab.sources import SourceFile

Cell = Callable[[str], Any]  # 열 이름 → 그 행의 칸 값 (없는 열이면 None)


def nothing_to_prepare(workbook: Any) -> Prepared:
    return Prepared()


def alone(finding: Finding, file_name: str) -> str:
    return ""


def always(finding: Finding) -> bool:
    return True


def default_facts(finding: Finding) -> list[tuple[str, str]]:
    """사용자 메시지에 넣을 지적 정보. File과 line은 fixer가 붙인다."""
    return [
        ("TYPE", finding.type),
        ("check", finding.check),
        ("detail", finding.detail),
        ("Group", finding.group),
        ("information", finding.information),
        ("Function", finding.function),
    ]


def no_tail(context: Any, findings: list[Finding], source: SourceFile) -> str:
    return ""


def first_line_region(text: str, findings: list[Finding]) -> tuple[int, int]:
    """묶음 첫 행의 줄로 보낼 범위를 정한다 (파일 전체, 함수, 앞뒤 60줄 중 하나)."""
    line = findings[0].line
    assert line is not None  # make_request가 먼저 확인한다
    return select_region(text, line)


@dataclass(frozen=True, eq=False)
class Kind:
    name: str  # DB와 폼에 쓰는 이름
    title: str  # 화면 이름
    sheet_word: str  # 결과 시트 이름에 들어가는 낱말 (소문자)
    required: tuple[str, ...]  # 필수 열 (polyspace.column_name으로 맞춘 이름)
    read_row: Callable[[Cell, int, Any], Finding | str]  # 대상이면 Finding, 아니면 뺀 이유
    label: Callable[[Finding], str]  # 배지 글자. 대상 고르기와 우선순위에도 쓴다
    choices: tuple[str, ...]  # 첫 화면에서 고르는 대상. 중요한 것부터 적는다
    defaults: tuple[str, ...]  # 처음에 체크된 대상
    colors: Mapping[str, str]  # 배지 글자 → 배지 색 (red / orange / gray / blue)
    system_prompt: str  # 시스템 메시지 (영어. reason만 한국어로 받는다)
    heading: str  # 사용자 메시지 첫 줄
    prompt_version: int  # 답 처리 방식을 바꾸면 올린다 (캐시 키)
    prepare: Callable[[Any], Prepared] = nothing_to_prepare  # 엑셀 전체에서 작업 정보 읽기
    # 함께 보낼 행의 기준 (행, 짝지은 소스 이름) → 묶음 키. ''이면 혼자 보낸다
    # 한 묶음은 한 소스 파일이어야 한다(작업자는 첫 행의 소스만 읽는다). 키에 소스 이름을 넣는다
    group_key: Callable[[Finding, str], str] = alone
    facts: Callable[[Finding], list[tuple[str, str]]] = default_facts
    # 사용자 메시지에서 Finding 목록 뒤, 코드 앞에 붙일 글 (작업 정보, 묶음의 행, 소스로)
    prompt_tail: Callable[[Any, list[Finding], SourceFile], str] = no_tail
    region: Callable[[str, list[Finding]], tuple[int, int]] = first_line_region
    answer_schema: Mapping[str, Any] = field(default_factory=lambda: FIX_SCHEMA)
    to_results: Callable[[dict[str, Any], Request], list[FixResult]] = edits_results
    timeout: float = FIX_TIMEOUT  # LLM 대기 시간(초)
    auto_select: Callable[[Finding], bool] = always  # "수정 모두 선택"이 고를 행인지

    def rank(self, label: str) -> int:
        """중요한 대상일수록 작은 값. choices에 없는 배지 글자는 맨 뒤다."""
        return self.choices.index(label) if label in self.choices else len(self.choices)

    def priority(self, finding: Finding) -> int:
        return self.rank(self.label(finding))

    def color(self, label: str) -> str:
        return self.colors.get(label, "gray")
