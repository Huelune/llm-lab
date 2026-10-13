"""CodeMetrics 결과 시트: 함수의 측정값이 한도를 넘은 행을 리팩터링으로 고친다.

지표 목록, 한도, 단계는 회사 양식이라 바뀔 수 있으므로 작업마다 엑셀(결과 시트와 조건 시트)에서
읽고 코드와 지시문에 고정하지 않는다. "클수록 나쁨" 같은 방향도 가정하지 않고 조건 시트의 범위로
단계를 정한다. 결과 시트에 올라온 행은 Polyspace가 한도를 넘었다고 본 행이다.
"""

from __future__ import annotations

import re
from typing import Any

from llm_lab.cfunction import (
    FunctionRange,
    braces_balanced,
    function_name,
    function_range,
    signature_text,
    squash_code,
)
from llm_lab.conditions import Metric, read_metrics
from llm_lab.fixer import (
    FIX_SCHEMA,
    WHOLE_FILE_MAX_LINES,
    Edit,
    EditMismatch,
    FixError,
    FixResult,
    Request,
    _has_numbers,
    _minimize,
    _strip_numbers,
    apply_edits,
    display_diff,
    read_decisions,
    share,
    split_lines,
    user_error,
)
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
from llm_lab.sources import SourceFile

REQUIRED = ("file", "function", "line", "check", "threshold", "actual value")
# 배지 글자 (이 도구가 정한 이름). 조건 시트의 판정 글은 카드 메모에 그대로 보인다
FAIL, MIDDLE, UNKNOWN = "Fail", "가운데", "단계 모름"
NO_FUNCTION = "파일 단위 지표 (Function 없음)"
PASSED = "조건 시트상 Pass"
# 지표 이름을 낱말로 비교할 때 빼는 낱말
STOP_WORDS = frozenset({"number", "of", "max", "maximum", "the"})

# 함수를 통째로 다시 써야 하므로 이보다 긴 함수는 LLM에 보내지 않는다 (답이 잘린다)
FUNCTION_MAX_LINES = 1000
TIMEOUT = 300.0  # 함수를 통째로 쓰는 답이라 RTE(120초)보다 오래 기다린다
# 두 번 물어도 쓸 수 없는 답이었을 때 화면 첫 줄에 보일 설명
CALLING_FIX = "LLM이 부르는 곳 수 지표를 고치려 했습니다."
SIGNATURE_CHANGED = "LLM이 함수 이름이나 인자를 바꿨습니다."
COMMENT_DROPPED = "LLM이 함수 위 주석을 지웠습니다."
BRACES = "LLM 코드의 중괄호 짝이 맞지 않습니다."

# 답 형식: 판단과 이유는 행마다(공통 형식과 같음), 수정은 바꿀 범위를 통째로 대신할 code
CODE_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": FIX_SCHEMA["properties"]["findings"],
        "code": {"type": "string"},
    },
    "required": ["findings", "code"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You refactor one C function so that its Polyspace code metrics fall within the project's \
limits, without changing what the function does.
Scope:
- Answer with "code": the complete new text for the line range named in the request \
(the function and the comment right above it). Lines outside that range stay as they are.
- Keep the function's name, parameters, return type and specifiers (such as static) \
exactly as they are, and keep the comment above it.
- You may add new static helper functions at the top of "code", above that comment. \
Give each a one-line comment and a name of at most 31 characters \
that is not used elsewhere in the file.
- Do not add global variables. Do not change other functions, other files or headers.
Keep the behavior:
- Move statements mechanically. Keep the order of evaluation, side effects, calls \
and accesses to globals and volatile objects. Keep every original comment.
- Pass the values a helper needs as parameters, and return changed values \
through pointers or the return value.
- Do not add early returns, goto, recursion or dynamic memory. New code must follow \
MISRA C:2012 (for example Rule 15.5 single point of exit and Rule 8.8 static).
- Every function in "code", including new helpers, must stay within all the limits given.
Decide for each numbered finding:
- fix: your code brings that metric within its limit.
- no_fix: it cannot be done within these rules, or the change would put the behavior \
at risk. Number of Calling Functions is always no_fix, because only its callers could change it.
Do not guess definitions (macros, types, other functions) that are not shown. \
If they matter, say so in reason.
Write each "reason" in Korean, one to three sentences that a reviewer can paste \
into the Polyspace comment of that finding. For fix, say what moved where and \
the rough new value. For no_fix, say why.
Answer with JSON only. "findings" has one entry for each numbered finding. \
"code" is "" when no finding is fix.
"""


def metric_words(name: str) -> frozenset[str]:
    """지표 이름 → 비교용 낱말 집합. 문장 부호, 복수 s, 흔한 낱말(number, of, max…)을 뺀다."""
    words = re.findall(r"[a-z0-9]+", name.casefold())
    return (
        frozenset(word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words)
        - STOP_WORDS
    )


CALLING = metric_words("Number of Calling Functions")
# Polyspace 지표 뜻(MathWorks 문서)에 따른 줄이는 요령. 회사 조건이 아니라 공개된 지식이다.
# 이 묶음에 나온 지표의 요령만 메시지에 붙인다.
TIPS = {
    metric_words("Cyclomatic Complexity"): (
        "Cyclomatic Complexity counts decision points plus one: each if, else if, for, while "
        "and ?:, and each case followed by break. && and || do not count. Move cohesive "
        "branches or case bodies into static helpers, or merge a nested if without else into "
        "one condition with &&."
    ),
    metric_words("Number of Called Functions"): (
        "Number of Called Functions counts the distinct functions called in the body, "
        "standard library functions included. Calls through function pointers and assert do "
        "not count. Group calls that belong together into a static helper."
    ),
    metric_words("Number of Call Levels"): (
        "Number of Call Levels is the deepest nesting of if, switch, for and while, not the "
        "call depth. Each else if adds a level. Move inner blocks into static helpers, or turn "
        "a long else-if chain on one value into a switch."
    ),
    CALLING: (
        "Number of Calling Functions counts the distinct callers. Changing callers is outside "
        "this task, so answer no_fix and say why many callers are expected for this function."
    ),
}


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


def facts(finding: Finding) -> list[tuple[str, str]]:
    """사용자 메시지에 넣을 값. File과 line은 fixer가 붙인다."""
    return [
        ("check", finding.check),
        ("Actual Value", finding.actual),
        ("Threshold", finding.threshold),
        ("stage", finding.stage),
        ("Function", finding.function),
    ]


def _function(text: str, findings: list[Finding]) -> FunctionRange:
    """묶음 함수의 바꿀 범위. 못 찾거나 너무 길면 FixError다 (그 묶음만 오류)."""
    first = findings[0]
    name = function_name(first.function)
    assert first.line is not None  # make_request가 먼저 확인한다
    found = function_range(text, first.line, name)
    if found is None:
        raise FixError(
            user_error(
                "엑셀의 함수를 소스에서 찾지 못했습니다.",
                "엑셀을 만든 것과 같은 버전의 소스 폴더를 골라 다시 분석하세요.",
                f"{name}, {first.line}번 줄",
            )
        )
    # 묶음 키는 파일:이름이라, 같은 이름의 정의가 둘이면(#if 갈래) 다른 함수의 행이 섞인다
    for other in findings[1:]:
        assert other.line is not None
        if not found.start <= other.line <= found.end:
            raise FixError(
                user_error(
                    "같은 함수의 행이 서로 다른 줄을 가리킵니다.",
                    "엑셀을 만든 것과 같은 버전의 소스 폴더인지, 같은 이름의 함수가 둘인지 "
                    "확인하세요.",
                    f"엑셀 {other.row}행: {other.line}번 줄 "
                    f"(함수 범위 {found.start}~{found.end}줄)",
                )
            )
    size = found.end - found.start + 1
    if size > FUNCTION_MAX_LINES:
        raise FixError(
            user_error(
                "함수가 너무 길어 자동으로 나누지 않았습니다.",
                "직접 나누거나 Polyspace comment에 사유를 적으세요.",
                f"{size}줄 (기준 {FUNCTION_MAX_LINES}줄)",
            )
        )
    return found


def region(text: str, findings: list[Finding]) -> tuple[int, int]:
    """파일이 WHOLE_FILE_MAX_LINES줄 이하면 파일 전체를, 넘으면 바꿀 범위만 보인다."""
    found = _function(text, findings)
    total = len(split_lines(text))
    return (1, total) if total <= WHOLE_FILE_MAX_LINES else (found.start, found.end)


def prompt_tail(context: Any, findings: list[Finding], source: SourceFile) -> str:
    """한도 표, 이 묶음 지표의 요령, 바꿀 줄 범위."""
    parts = []
    limits = (context or {}).get("limits") or []
    if limits:
        listed = "\n".join(f"- {name}: {limit}" for name, limit in limits)
        parts.append(
            "Limits every function must meet, including new helpers "
            f"(read from this project's workbook):\n{listed}"
        )
    present = {metric_words(finding.check) for finding in findings}
    tips = [tip for words, tip in TIPS.items() if words in present]
    if tips:
        parts.append("About these metrics:\n" + "\n".join(f"- {tip}" for tip in tips))
    found = _function(source.text, findings)
    name = function_name(findings[0].function)
    parts.append(
        f"Replace lines {found.start}-{found.end} of {source.name} (function {name} and the "
        'comment right above it) with "code". Every other line stays as it is.'
    )
    return "\n\n".join(parts)


def to_results(data: dict[str, Any], request: Request) -> list[FixResult]:
    """행별 판단과, fix가 하나라도 있으면 code로 만든 묶음 공통 수정.

    fix가 없으면 code는 버린다.
    """
    decisions = read_decisions(data, len(request.findings))
    pairs = zip(decisions, request.findings, strict=True)
    for index, ((decision, _), finding) in enumerate(pairs, start=1):
        if decision == "fix" and metric_words(finding.check) == CALLING:
            raise EditMismatch(
                f"Finding {index}은 Number of Calling Functions라 no_fix여야 함",
                "format",
                CALLING_FIX,
            )
    if not any(decision == "fix" for decision, _ in decisions):
        return share(decisions, (), "")
    edits, diff = _code_edits(data.get("code"), request)
    return share(decisions, edits, diff)


def _code_edits(code: Any, request: Request) -> tuple[tuple[Edit, ...], str]:
    """바꿀 범위를 code로 바꾼 뒤, 실제로 바뀐 줄만 남긴 수정과 화면용 diff."""
    source = request.source
    if not isinstance(code, str) or not code.strip():
        raise EditMismatch("fix인데 code가 비었음", "format")
    code = code.replace("\r\n", "\n")
    if _has_numbers(code):
        # 보인 코드의 줄 번호 표시('   57| ')까지 베꼈으면 뗀다. 새로 쓴 도우미 줄에는 번호가
        # 없을 수 있어 줄마다 본다 (fixer.locate_edits와 같은 규칙)
        code = _strip_numbers(code)
    try:
        code.encode(source.encoding)
    except UnicodeEncodeError as exc:
        char = ord(code[exc.start])
        raise EditMismatch(
            f"code에 {source.encoding}로 쓸 수 없는 문자 U+{char:04X}가 있음 - "
            "ASCII나 한글만 쓰세요",
            "encoding",
        ) from None
    if not braces_balanced(code):
        raise EditMismatch("code의 중괄호 짝이 맞지 않음", "format", BRACES)
    text = source.text
    found = _function(text, request.findings)
    squashed = squash_code(code)
    index = squashed.find(signature_text(text, found))
    # 시그니처 앞에 낱말(static, inline, 다른 반환형 등)이 붙었어도 시그니처가 바뀐 것이다
    if index < 0 or re.search(r"\w$", squashed[:index].rstrip()):
        raise EditMismatch(
            "함수 시그니처(이름, 인자, 반환형)가 바뀜 - 그대로 두세요", "format", SIGNATURE_CHANGED
        )
    lines = split_lines(text)
    comment = " ".join("\n".join(lines[found.start - 1 : found.signature - 1]).split())
    if comment not in " ".join(code.split()):
        raise EditMismatch(
            "함수 바로 위 주석이 빠지거나 바뀜 - 그대로 두세요", "format", COMMENT_DROPPED
        )
    begin = sum(len(line) + 1 for line in lines[: found.start - 1])
    end = min(len(text), sum(len(line) + 1 for line in lines[: found.end]))
    # 범위 끝 줄바꿈과 code 끝 줄바꿈을 맞춘다 (안 맞추면 다음 줄이 붙거나 빈 줄이 생긴다)
    code = code.rstrip("\n") + ("\n" if text[end - 1 : end] == "\n" else "")
    edits = tuple(sorted(_minimize(text, Edit(begin, end, code)), key=lambda edit: edit.start))
    if not edits:
        raise EditMismatch("수정해도 원본과 같음")
    return edits, display_diff(source.name, text, apply_edits(text, edits))


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
    facts=facts,
    prompt_tail=prompt_tail,
    region=region,
    answer_schema=CODE_SCHEMA,
    to_results=to_results,
    timeout=TIMEOUT,
    uses_definitions=False,  # 함수 하나를 나누는 일이라 정의가 필요 없다
    auto_select=not_middle,
)
