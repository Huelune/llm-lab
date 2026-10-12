"""CodeMetrics 결과 시트: 함수의 측정값이 한도를 넘은 행을 리팩터링으로 고친다.

지표 목록, 한도, 단계는 회사 양식이라 바뀔 수 있으므로 작업마다 엑셀(결과 시트와 조건 시트)에서
읽고 코드와 지시문에 고정하지 않는다. "클수록 나쁨" 같은 방향도 가정하지 않고 조건 시트의 범위로
단계를 정한다. 결과 시트에 올라온 행은 Polyspace가 한도를 넘었다고 본 행이다.
"""

from __future__ import annotations

import re
from typing import Any

from llm_lab.cfunction import function_name
from llm_lab.conditions import Metric, read_metrics
from llm_lab.kinds.base import Cell, Kind
from llm_lab.polyspace import (
    Finding,
    Prepared,
    cell_text,
    data_rows,
    find_header,
    line_number,
    norm,
)

REQUIRED = ("file", "function", "line", "check", "threshold", "actual value")
# 배지 글자 (이 도구가 정한 이름). 조건 시트의 판정 글은 카드 메모에 그대로 보인다
FAIL, MIDDLE, UNKNOWN = "Fail", "가운데", "단계 모름"
NO_FUNCTION = "파일 단위 지표 (Function 없음)"
PASSED = "조건 시트상 Pass"
# 지표 이름을 낱말로 비교할 때 빼는 낱말
STOP_WORDS = frozenset({"number", "of", "max", "maximum", "the"})

SYSTEM_PROMPT = """\
You refactor one C function so that its Polyspace code metrics fall within the project's \
limits, without changing what the function does.
"""


def metric_words(name: str) -> frozenset[str]:
    """지표 이름 → 비교용 낱말 집합. 문장 부호, 복수 s, 흔한 낱말(number, of, max…)을 뺀다."""
    words = re.findall(r"[a-z0-9]+", name.casefold())
    return (
        frozenset(word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words)
        - STOP_WORDS
    )


def number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _result_thresholds(workbook: Any) -> dict[str, list[str]]:
    """결과 시트의 Check → 그 Check에 나온 Threshold 글 (처음 나온 순서, 중복 없이).

    read_sheet와 같은 규칙으로 결과 시트를 고른다: 이름이 _Result로 끝나고 metric이 들어간 시트.
    """
    for sheet in workbook.worksheets:
        title = sheet.title.casefold()
        if not title.endswith("_result") or "metric" not in title:
            continue
        found = find_header(sheet, REQUIRED)
        if isinstance(found, str):
            continue
        thresholds: dict[str, list[str]] = {}
        for _, cell in data_rows(sheet, *found):
            check, text = cell_text(cell("check")), cell_text(cell("threshold"))
            values = thresholds.setdefault(check, [])
            if text and text not in values:
                values.append(text)
        thresholds.pop("", None)
        return thresholds
    return {}


def _edge(metric: Metric) -> float | None:
    """Pass 범위의 끝값: 위 끝이 있으면 위 끝, 없으면 아래 끝 (이상이어야 하는 지표)."""
    band = metric.pass_band
    if band is None:
        return None
    return band.high if band.high is not None else band.low


def pair_metrics(
    thresholds: dict[str, list[str]], metrics: list[Metric]
) -> tuple[dict[str, int], list[str]]:
    """결과 시트 Check(norm) → 조건 시트 지표 번호와, 값으로 이은 Check 목록.

    낱말 집합이 같으면 잇는다. 남은 것끼리는 Check의 Threshold가 한 가지이고, 그 값과 Pass 범위의
    끝값이 같은 지표가 서로 하나뿐일 때만 잇는다.
    """
    pairs: dict[str, int] = {}
    for check in thresholds:
        hits = [i for i, m in enumerate(metrics) if metric_words(m.name) == metric_words(check)]
        if len(hits) == 1:
            pairs[norm(check)] = hits[0]
    used = set(pairs.values())
    single: dict[str, float] = {}
    for check, texts in thresholds.items():
        values = {number(text) for text in texts}
        if norm(check) not in pairs and len(values) == 1 and None not in values:
            single[check] = values.pop()  # type: ignore[assignment]
    by_value: list[str] = []
    for check, value in single.items():
        hits = [i for i, m in enumerate(metrics) if i not in used and _edge(m) == value]
        rivals = [other for other, limit in single.items() if limit == value]
        if len(hits) == 1 and len(rivals) == 1:
            pairs[norm(check)] = hits[0]
            by_value.append(check)
    return pairs, by_value


def _limits(
    metrics: list[Metric], thresholds: dict[str, list[str]], pairs: dict[str, int]
) -> list[list[str]]:
    """LLM에 줄 (지표 이름, 지켜야 할 범위). 조건 시트의 모든 지표와, 짝이 없는 결과 시트 Check."""
    checks: dict[int, list[str]] = {}
    for check in thresholds:
        index = pairs.get(norm(check))
        if index is not None:
            checks.setdefault(index, []).append(check)
    limits = []
    for index, metric in enumerate(metrics):
        band = metric.pass_band
        if band is None:
            continue
        others = [check for check in checks.get(index, []) if check != metric.name]
        title = f"{', '.join(others)} ({metric.name})" if others else metric.name
        limits.append([title, f"Pass range {band.text}"])
    for check, texts in thresholds.items():
        if norm(check) not in pairs:
            limits.append([check, "Threshold " + ", ".join(texts)])
    return limits


def prepare(workbook: Any) -> Prepared:
    """조건 시트의 단계, 결과 시트 Check와의 짝, LLM에 줄 한도 표를 작업 정보로 만든다."""
    metrics, note = read_metrics(workbook)
    thresholds = _result_thresholds(workbook)
    pairs, by_value = pair_metrics(thresholds, metrics)
    notes = [note]
    unpaired = [check for check in thresholds if norm(check) not in pairs]
    if metrics and unpaired:
        notes.append(f"조건 시트에서 짝을 찾지 못한 지표 {len(unpaired)}개")
    if by_value:
        notes.append(f"값으로 이은 지표 {len(by_value)}개")
    data = {
        "metrics": [metric.to_json() for metric in metrics],
        "pairs": pairs,
        "limits": _limits(metrics, thresholds, pairs),
    }
    return Prepared(data, " · ".join(notes))


def _stage(context: Any, check: str, actual: str) -> tuple[str, str, str]:
    """(배지 글자, 조건 시트 판정 글, 단계 모름 이유). Pass면 배지 글자가 'pass'다."""
    data = context or {}
    metrics = data.get("metrics") or []
    if not metrics:
        return UNKNOWN, "", "조건 시트 없음"
    index = (data.get("pairs") or {}).get(norm(check))
    if index is None:
        return UNKNOWN, "", "조건 시트에 짝이 없는 지표"
    value = number(actual)
    if value is None:
        return UNKNOWN, "", "Actual Value가 숫자가 아님"
    metric = Metric.from_json(metrics[index])
    band = metric.stage(value)
    if band is None:
        unread = any(not other.ok for other in metric.bands)
        return UNKNOWN, "", "범위 글을 읽지 못함" if unread else "어느 범위에도 들지 않음"
    return {"pass": "pass", "fail": FAIL}.get(band.kind, MIDDLE), band.verdict, ""


def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
    """Function이 있고 조건 시트상 Pass가 아닌 행이 대상이다. 단계는 category에 담는다."""
    function = cell_text(cell("function"))
    if not function_name(function):
        return NO_FUNCTION
    check = cell_text(cell("check"))
    threshold, actual = cell_text(cell("threshold")), cell_text(cell("actual value"))
    label, verdict, unknown = _stage(context, check, actual)
    if label == "pass":
        return PASSED
    note = f"값 {actual} · 기준 {threshold}"
    if verdict:
        note += f" · 조건 시트: {verdict}"
    if unknown:
        note += f" · 단계 모름: {unknown}"
    return Finding(
        row=row,
        color="",
        type="",
        file=cell_text(cell("file")),
        line=line_number(cell("line")),
        check=check,
        detail="",
        function=function,
        status=cell_text(cell("status")),
        comment=cell_text(cell("comment")),
        category=label,
        note=note,
        threshold=threshold,
        actual=actual,
        stage=verdict,
    )


def stage_of(finding: Finding) -> str:
    return finding.category or UNKNOWN


def same_function(finding: Finding, file_name: str) -> str:
    """같은 파일의 같은 함수에 걸린 행끼리 묶는다 (함수 하나에 LLM 한 번)."""
    return f"{file_name}:{function_name(finding.function)}"


def not_middle(finding: Finding) -> bool:
    """가운데 단계 행은 사람이 검토해서 고른다."""
    return finding.category != MIDDLE


CODEMETRICS = Kind(
    name="codemetrics",
    title="CodeMetrics",
    sheet_word="metric",
    required=REQUIRED,
    read_row=read_row,
    label=stage_of,
    choices=(FAIL, MIDDLE, UNKNOWN),
    defaults=(FAIL, MIDDLE, UNKNOWN),
    colors={FAIL: "red", MIDDLE: "orange", UNKNOWN: "gray"},
    system_prompt=SYSTEM_PROMPT,
    heading="Polyspace code metrics over their limits in one C function",
    prompt_version=1,
    prepare=prepare,
    group_key=same_function,
    auto_select=not_middle,
)
