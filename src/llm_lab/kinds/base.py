"""시트 종류(Kind): 종류마다 다른 것(읽을 시트·열, 분석할 행, 배지)을 한곳에 모은 정의."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from llm_lab.polyspace import Finding, Prepared

Cell = Callable[[str], Any]  # 열 이름 → 그 행의 칸 값 (없는 열이면 None)


def nothing_to_prepare(workbook: Any) -> Prepared:
    return Prepared()


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
    prepare: Callable[[Any], Prepared] = nothing_to_prepare  # 엑셀 전체에서 작업 정보 읽기

    def rank(self, label: str) -> int:
        """중요한 대상일수록 작은 값. choices에 없는 배지 글자는 맨 뒤다."""
        return self.choices.index(label) if label in self.choices else len(self.choices)

    def priority(self, finding: Finding) -> int:
        return self.rank(self.label(finding))

    def color(self, label: str) -> str:
        return self.colors.get(label, "gray")
