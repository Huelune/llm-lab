"""Polyspace 결과 엑셀의 조건 시트를 읽는다. 지금은 Code Metrics 조건(지표별 단계)만 읽는다.

조건 값(지표 목록, 범위, 단계 수)과 열 배치는 바뀔 수 있으므로 작업마다 엑셀에서 읽고, 코드에는 값을
고정하지 않는다. 시트는 이름이 아니라 열(범위 열과 판정 열)로 찾고, 열은 머리 글로 찾는다.
병합 셀이 남긴 빈 열과, 지표 이름이 첫 단계 행에만 있는 모양을 견딘다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# 헤더 위에 제목 행이 있어도 찾도록 위에서부터 이만큼 훑는다
HEADER_SCAN_ROWS = 30
RANGE_HEADERS = {"기준", "기준치", "기준값", "기준 범위", "범위", "threshold", "range"}
VERDICT_HEADERS = {"판정", "결과", "verdict", "result"}  # 그 밖에 pass나 fail이 든 머리
NAME_HEADERS = {
    "metric", "metrics", "metric name", "check", "name", "지표", "지표명", "지표 이름",
    "메트릭", "메트릭명", "항목", "항목명", "이름",
}  # fmt: skip
NO_HEADERS = {"no", "no.", "번호", "순번", "#"}
PASS_WORDS = ("pass", "통과", "합격")
FAIL_WORDS = ("fail", "실패", "불합격")

_NUM = r"[-+]?\d+(?:\.\d+)?"
_SPAN = re.compile(rf"({_NUM})[~-]({_NUM})")
_COMPARE = re.compile(rf"(>=|>|<=|<|=)({_NUM})")
_KOREAN = re.compile(rf"({_NUM})(이상|이하|초과|미만)")
_EXACT = re.compile(_NUM)


@dataclass(frozen=True)
class Band:
    """지표의 단계 하나. 범위 글을 읽지 못하면 low와 high가 모두 None이다."""

    low: float | None  # None이면 아래 끝 없음
    high: float | None  # None이면 위 끝 없음
    low_open: bool  # True면 low 자체는 들지 않음 ('>', '초과')
    high_open: bool  # True면 high 자체는 들지 않음 ('<', '미만')
    text: str  # 범위 글 원문
    verdict: str  # 판정 글 원문
    kind: str  # "pass" | "middle" | "fail"

    @property
    def ok(self) -> bool:
        return self.low is not None or self.high is not None

    def contains(self, value: float) -> bool:
        if not self.ok:
            return False
        if self.low is not None and (value <= self.low if self.low_open else value < self.low):
            return False
        return self.high is None or (value < self.high if self.high_open else value <= self.high)


@dataclass(frozen=True)
class Metric:
    name: str
    bands: tuple[Band, ...]

    @property
    def pass_band(self) -> Band | None:
        return next((band for band in self.bands if band.kind == "pass"), None)

    def stage(self, value: float) -> Band | None:
        """값이 든 첫 단계. 어느 범위에도 들지 않으면 None."""
        return next((band for band in self.bands if band.contains(value)), None)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Metric:
        return cls(data["name"], tuple(Band(**band) for band in data["bands"]))


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _cell(cells: tuple[Any, ...], index: int) -> str:
    return _text(cells[index]) if index < len(cells) else ""


def _squash(title: str) -> str:
    return "".join(ch for ch in title.casefold() if ch.isalnum())


def _header(value: Any) -> str:
    """머리 글 비교용: 대소문자·공백·끝 콜론을 무시하고 '/' 앞뒤 공백을 없앤다."""
    text = _text(value).casefold().rstrip(": ").strip()
    return re.sub(r"\s*/\s*", "/", text)


def parse_range(text: str) -> tuple[float | None, float | None, bool, bool] | None:
    """범위 글 → (하한, 상한, 하한 미포함, 상한 미포함). 읽지 못하면 None.

    `1 ~ 15`, `3-8`, `>= 31`, `>=11`, `=> 4`, `> 5`, `≤ 7`, `< 10%`, `20 이상`, `20 미만`, `0`.
    """
    plain = (
        text.replace("～", "~").replace("≥", ">=").replace("≤", "<=")
        .replace("=>", ">=").replace("=<", "<=")
    )  # fmt: skip
    plain = re.sub(r"[\s,%]", "", plain)
    if match := _SPAN.fullmatch(plain):
        return float(match[1]), float(match[2]), False, False
    if match := _COMPARE.fullmatch(plain):
        sign, number = match[1], float(match[2])
        if sign == "=":
            return number, number, False, False
        if sign.startswith(">"):
            return number, None, sign == ">", False
        return None, number, False, sign == "<"
    if match := _KOREAN.fullmatch(plain):
        number, word = float(match[1]), match[2]
        if word in ("이상", "초과"):
            return number, None, word == "초과", False
        return None, number, False, word == "미만"
    if _EXACT.fullmatch(plain):
        return float(plain), float(plain), False, False
    return None


def verdict_kind(verdict: str) -> str:
    """판정 글 → pass | fail | middle. 둘 다 들었거나 둘 다 없으면 middle."""
    text = verdict.casefold()
    has_fail = any(word in text for word in FAIL_WORDS)
    for word in FAIL_WORDS:  # '불합격' 안의 '합격'을 pass로 보지 않도록
        text = text.replace(word, " ")
    has_pass = any(word in text for word in PASS_WORDS)
    if has_pass != has_fail:
        return "pass" if has_pass else "fail"
    return "middle"


@dataclass(frozen=True)
class _Layout:
    header: int  # 헤더 행 번호 (1부터)
    name: int
    range: int
    verdict: int


def _layout(cells: tuple[Any, ...], number: int) -> _Layout | None:
    heads = [_header(cell) for cell in cells]
    range_col = next((i for i, h in enumerate(heads) if h in RANGE_HEADERS), None)
    verdict_col = next(
        (i for i, h in enumerate(heads) if h in VERDICT_HEADERS or "pass" in h or "fail" in h),
        None,
    )
    if range_col is None or verdict_col is None:
        return None
    no_col = next((i for i, h in enumerate(heads) if h in NO_HEADERS), None)
    others = [i for i, h in enumerate(heads) if h and i not in (range_col, verdict_col, no_col)]
    named = [i for i in others if heads[i] in NAME_HEADERS]
    after_no = [i for i in others if no_col is not None and i > no_col]
    name_col = (named or after_no or others or [None])[0]
    if name_col is None:
        return None
    return _Layout(number, name_col, range_col, verdict_col)


def _find(workbook: Any) -> tuple[Any, _Layout] | None:
    found: list[tuple[Any, _Layout]] = []
    for sheet in workbook.worksheets:
        if sheet.title.casefold().endswith("_result"):
            continue
        rows = sheet.iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True)
        for number, cells in enumerate(rows, start=1):
            if layout := _layout(cells, number):
                found.append((sheet, layout))
                break
    if not found:
        return None
    return next((item for item in found if "metric" in _squash(item[0].title)), found[0])


def read_metrics(workbook: Any) -> tuple[list[Metric], str]:
    """(지표 목록, 작업 메모 한 줄). 조건 시트가 없거나 지표가 없으면 ([], 이유)."""
    found = _find(workbook)
    if found is None:
        return [], "조건 시트 없음: 결과 시트 Threshold만 씀"
    sheet, layout = found
    metrics: list[tuple[str, list[Band]]] = []
    for cells in sheet.iter_rows(min_row=layout.header + 1, values_only=True):
        name = _cell(cells, layout.name)
        text, verdict = _cell(cells, layout.range), _cell(cells, layout.verdict)
        if name:
            metrics.append((name, []))
        if not (text or verdict) or not metrics:
            continue  # 빈 행, 또는 지표 이름보다 먼저 나온 단계 행
        parsed = parse_range(text) or (None, None, False, False)
        metrics[-1][1].append(Band(*parsed, text, verdict, verdict_kind(verdict)))

    result = [Metric(name, tuple(bands)) for name, bands in metrics]
    if not result:
        return [], "조건 시트에 읽을 지표 없음: 결과 시트 Threshold만 씀"
    unread = sum(not band.ok for metric in result for band in metric.bands)
    note = f"조건 시트: 지표 {len(result)}개"
    return result, note + (f" (범위 글을 읽지 못한 단계 {unread}개)" if unread else "")
