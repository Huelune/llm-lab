"""Polyspace 결과 시트 종류. 새 종류는 이 패키지에 파일 하나를 더하고 KINDS에 넣는다."""

from llm_lab.kinds.base import Cell, Kind
from llm_lab.kinds.rte import RTE

KINDS: dict[str, Kind] = {kind.name: kind for kind in (RTE,)}

__all__ = ["KINDS", "RTE", "Cell", "Kind"]
