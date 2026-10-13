"""RTE(런타임 오류) 결과 시트: Polyspace Code Prover의 Red·Orange 검사.

같은 파일의 같은 함수에 걸린 검사는 한 번에 보낸다. 같은 줄의 수정이 서로 겹치지 않고,
한 원인에서 나온 여러 검사를 한 번에 고치기 위해서다.
"""

from __future__ import annotations

from typing import Any

from llm_lab.fixer import select_region
from llm_lab.kinds.base import Cell, Kind
from llm_lab.polyspace import Finding, cell_text, line_number, norm

SYSTEM_PROMPT = """\
You review the Polyspace Code Prover run-time checks found in one C function \
and decide for each whether the code must change. \
All findings in one request are in the same function.
- Red Check: Polyspace proved that the operation fails whenever it runs. Fix it.
- Orange Check: Polyspace could not prove the operation safe. \
Fix it if some execution can really fail. \
If the code shown guarantees safety, answer no_fix and name the lines that guarantee it.
When you fix:
- Make the safety provable by static analysis: explicit range checks, \
division-by-zero checks, initialization and similar.
- One set of edits is shared by every finding you answer fix, so fix them together. \
When several findings come from one cause in the code shown, fix that cause once \
instead of each use.
- Change as little as possible. Do not touch lines unrelated to the findings. \
Keep the behavior for valid inputs.
- Copy each edit's "original" exactly from the code shown, as whole consecutive lines, \
without the line-number prefix. "replacement" is the new text for those lines.
- Keep each edit small: "original" holds only the lines you change, not the whole function. \
Add one unchanged neighbouring line only when the changed line appears more than once.
Do not guess definitions (macros, types, other functions) that are not shown. \
If they matter, say so in reason.
Write each "reason" in Korean, one to three sentences that a reviewer can paste \
into the Polyspace comment of that finding.
Answer with JSON only. "findings" has one entry for each numbered finding. \
"edits" is [] when no finding is fix.
"""

# TYPE 칸의 글자로 대상을 고른다 (셀 배경색은 보지 않는다)
TARGET_TYPES = {"red check": "Red", "orange check": "Orange"}


def read_row(cell: Cell, row: int, context: Any) -> Finding | str:
    """Red·Orange Check면 Finding, 그 밖(Gray Check 등)이면 TYPE 글자가 뺀 이유다."""
    color = TARGET_TYPES.get(norm(cell("type")))
    if color is None:
        return cell_text(cell("type")) or "(빈 TYPE)"
    return Finding(
        row=row,
        color=color,
        type=cell_text(cell("type")),
        file=cell_text(cell("file")),
        line=line_number(cell("line")),
        check=cell_text(cell("check")),
        detail=cell_text(cell("detail")),
        id=cell_text(cell("id")),
        group=cell_text(cell("group")),
        information=cell_text(cell("information")),
        function=cell_text(cell("function")),
        status=cell_text(cell("status")),
        comment=cell_text(cell("comment")),
    )


def color_of(finding: Finding) -> str:
    return finding.color


def same_function(finding: Finding, file_name: str) -> str:
    """같은 파일의 같은 함수에 걸린 행끼리 묶는다. Function이 비면 혼자다.

    C는 대소문자를 가리므로 이름은 공백만 정리하고 그대로 비교한다.
    """
    name = " ".join(finding.function.split())
    return f"{file_name}:{name}" if name else ""


def region(text: str, findings: list[Finding]) -> tuple[int, int]:
    """묶음의 모든 행이 보이도록, 행마다 고른 범위를 하나로 합친다."""
    first = last = 0
    for finding in findings:
        assert finding.line is not None  # make_request가 먼저 확인한다
        start, end = select_region(text, finding.line)
        first = start if not first else min(first, start)
        last = max(last, end)
    return first, last


RTE = Kind(
    name="rte",
    title="RTE",
    sheet_word="rte",
    required=("type", "file", "line", "check", "detail"),
    read_row=read_row,
    label=color_of,
    choices=("Red", "Orange"),
    defaults=("Red", "Orange"),
    colors={"Red": "red", "Orange": "orange"},
    system_prompt=SYSTEM_PROMPT,
    heading="Polyspace Code Prover results in one C function",
    # 4: 공통 답 형식 (findings + edits)
    prompt_version=4,
    group_key=same_function,
    region=region,
)
