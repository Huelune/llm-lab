"""MISRA C:2012 결과 시트와 조건 시트.

결과 시트의 열은 RTE와 같고 ID만 없다. 규칙마다의 프로젝트 분류(Mode)와 적용 여부(Enabled)는
엑셀의 조건 시트에서 작업마다 읽는다. 바뀔 수 있으므로 코드에 고정하지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

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

CATEGORIES = ("Mandatory", "Required", "Advisory")  # MISRA 표준 분류, 중요한 것부터
NO_CATEGORY = "분류 없음"
DISABLED = "적용 안 함 (조건 시트)"
NO_CONDITIONS = "조건 시트 없음: 결과 시트 분류를 씀"
# 조건 시트 Enabled 칸에서 꺼짐으로 보는 값 (공백 정리, 대소문자 무시). 그 밖은 켜짐이다
OFF_WORDS = {"no", "n", "off", "false", "0", "disabled"}
# 글 맨 앞의 규칙 번호: "10.3 …", "Rule 10.3", "Dir 4.1", "D4.1", "Directive 4.1"
RULE = re.compile(r"\s*(?:rule\s*)?(dir(?:ective)?\s*|d\s*)?(\d+(?:\.\d+)+)", re.IGNORECASE)

SYSTEM_PROMPT = """\
You review Polyspace results for MISRA C:2012 in C code. \
All findings in one request are on the same line. \
For each finding, decide whether the code must change.
- "check" gives the rule number and its headline, "detail" what Polyspace found, \
and "category" the rule category this project uses.
- Mandatory: no deviation is allowed, so fix it. If the code shown is not enough to fix it, \
answer no_fix and say in reason that it must be fixed by hand and what is missing.
- Required and Advisory: answer fix when a small change removes the violation \
without changing behavior. Otherwise answer no_fix with a deviation rationale.
When you fix:
- Keep the behavior the same for every input. Typical fixes are explicit casts \
to the intended essential type, U suffixes on unsigned constants, \
added parentheses or braces, and splitting a statement.
- Use the type names the code already uses, such as its typedefs.
- Do not add new MISRA C:2012 violations. \
For example, do not cast a composite expression (Rule 10.8); cast its operands.
- One set of edits is shared by every finding you answer fix, so fix them together.
- Change as little as possible. Do not touch lines unrelated to the findings.
- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
without the line-number prefix. "replacement" is the new text for those lines.
- Keep each edit small: "original" holds only the lines you change. \
Add one unchanged neighbouring line only when the changed line appears more than once.
Do not guess definitions (macros, types, other functions) that are not shown. \
If they matter, say so in reason.
Write each "reason" in Korean, one to three sentences that a reviewer can paste \
into the Polyspace comment of that finding. For fix, say what changed. \
For no_fix, write why the code is acceptable as written.
Answer with JSON only. "findings" has one entry for each numbered finding. \
"edits" is [] when no finding is fix.
"""


def rule_key(text: str, directive: bool = False) -> str | None:
    """글 맨 앞의 규칙 번호. 디렉티브면 앞에 D를 붙여 Rule 4.1과 Dir 4.1을 가른다.

    맨 앞에 번호가 없으면 None이다. 규칙 문장 속의 숫자는 뽑지 않는다.
    """
    match = RULE.match(text)
    if match is None:
        return None
    return ("D" if match.group(1) or directive else "") + match.group(2)


def category_word(text: str) -> str:
    """'Category: Required', 'required' → 'Required'. 분류 낱말이 없으면 ''."""
    words = re.findall(r"[a-z]+", text.casefold())
    return next((name for name in CATEGORIES if name.casefold() in words), "")


def read_conditions(workbook: Any) -> Prepared:
    """조건 시트에서 규칙 번호 → [분류, 켜짐]을 읽는다.

    조건 시트는 이름이 _Result로 끝나지 않고 guideline·mode 열이 있는 시트다. 이름은 고정하지
    않고, 여럿이면 이름에 misra가 든 시트를 쓴다.
    """
    found = []
    for sheet in workbook.worksheets:
        if sheet.title.casefold().endswith("_result"):
            continue
        header = find_header(sheet, ("guideline", "mode"))
        if not isinstance(header, str):
            found.append((sheet, *header))
    if not found:
        return Prepared({}, NO_CONDITIONS)
    sheet, header_row, columns = next(
        (item for item in found if "misra" in item[0].title.casefold()), found[0]
    )
    rules: dict[str, list[Any]] = {}
    repeated: set[str] = set()
    for _, cell in data_rows(sheet, header_row, columns):
        key = rule_key(cell_text(cell("guideline")))
        mode = " ".join(cell_text(cell("mode")).split())
        if key is None or not mode:
            continue
        if key in rules:
            # 숫자 칸으로 저장된 "8.10"은 8.1로 읽혀 진짜 8.1과 겹친다.
            # 어느 쪽인지 알 수 없으므로 그 번호는 쓰지 않는다
            repeated.add(key)
        category = category_word(mode) or mode[:1].upper() + mode[1:]
        rules[key] = [category, norm(cell("enabled")) not in OFF_WORDS]
    for key in repeated:
        del rules[key]
    if not rules:
        return Prepared({}, NO_CONDITIONS)
    note = f"조건 시트: 규칙 {len(rules)}개"
    if repeated:
        note += f" · 번호가 겹친 {len(repeated)}개는 결과 시트 분류를 씀"
    return Prepared(rules, note)


def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
    """모든 행이 대상이다. 다만 조건 시트에서 꺼진 규칙의 행은 뺀다.

    분류는 조건 시트의 Mode가 먼저고, 없으면 결과 시트 information의 분류 낱말이다.
    """
    check = cell_text(cell("check"))
    group = cell_text(cell("group"))
    information = cell_text(cell("information"))
    key = rule_key(check, "directive" in group.casefold())
    rule = (context or {}).get(key) if key else None
    if rule is not None and not rule[1]:
        return DISABLED
    reported = category_word(information)
    category = rule[0] if rule is not None else reported or NO_CATEGORY
    note = ""
    if rule is not None and reported and reported != category:
        note = f"분류: 조건 시트 {category} (결과 시트 {reported})"
    return Finding(
        row=row,
        color="",
        type=cell_text(cell("type")),
        file=cell_text(cell("file")),
        line=line_number(cell("line")),
        check=check,
        detail=cell_text(cell("detail")),
        id=cell_text(cell("id")),
        group=group,
        information=information,
        function=cell_text(cell("function")),
        status=cell_text(cell("status")),
        comment=cell_text(cell("comment")),
        category=category,
        note=note,
    )


def category_of(finding: Finding) -> str:
    return finding.category or NO_CATEGORY


def same_line(finding: Finding, file_name: str) -> str:
    """같은 파일의 같은 줄에 걸린 행끼리 묶는다. 줄 번호가 없으면 혼자다."""
    return f"{file_name}:{finding.line}" if finding.line is not None else ""


def facts(finding: Finding) -> list[tuple[str, str]]:
    """information 대신 실제 분류를 보낸다. 두 값이 다를 때 LLM이 헷갈리지 않게 하기 위해서다."""
    category = "" if finding.category == NO_CATEGORY else finding.category
    return [
        ("TYPE", finding.type),
        ("check", finding.check),
        ("detail", finding.detail),
        ("Group", finding.group),
        ("category", category),
        ("Function", finding.function),
    ]


MISRA = Kind(
    name="misra",
    title="MISRA C:2012",
    sheet_word="misra",
    required=("type", "file", "line", "check", "detail"),
    read_row=read_row,
    label=category_of,
    choices=CATEGORIES,
    defaults=("Mandatory", "Required"),
    colors={"Mandatory": "red", "Required": "orange", "Advisory": "gray"},
    system_prompt=SYSTEM_PROMPT,
    heading="Polyspace MISRA C:2012 results",
    prompt_version=1,
    prepare=read_conditions,
    group_key=same_line,
    facts=facts,
)
