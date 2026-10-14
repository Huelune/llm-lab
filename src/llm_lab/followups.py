"""LLM이 답의 need 칸으로 보이지 않는 정의를 되묻게 하는 규칙.

지시문에 되묻기 규칙을 붙이고, 답 형식에 need를 더하고, 답에서 이름을 꺼낸다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

MAX_ASKED_NAMES = 10  # 한 번에 받는 이름 수
NEED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 세 종류 지시문에 공통으로 있는 문장. 되묻기를 켜면 ASK_RULE로 바꾼다
GUESS_RULE = (
    "Do not guess definitions (macros, types, other functions) that are not shown. "
    "If they matter, say so in reason."
)
ASK_RULE = (
    'Do not guess definitions that are not shown; ask for them in "need". '
    "If a name is not in the project, treat it as external code whose behavior is unknown, "
    "and say so in reason."
)
JSON_RULE = "Answer with JSON only."  # 되묻기 규칙은 이 문장 앞에 붙인다


def follow_up_rules(rounds: int) -> str:
    """지시문에 붙이는 되묻기 규칙 문단."""
    times = "time" if rounds == 1 else "times"
    return (
        "If you need a definition that is not shown to decide (a function body, a macro, "
        f'a type or a variable), put its exact name in "need", at most {MAX_ASKED_NAMES} names. '
        f"You can ask {rounds} {times} in total. "
        'When "need" is not empty your findings are provisional: the definitions will be sent '
        "and you will answer again. Do not ask for names already shown. "
        'Leave "need" empty when the code shown is enough.\n'
    )


def with_follow_ups(prompt: str, rounds: int) -> str:
    """되묻기를 켠 지시문. rounds가 0이면 prompt 그대로."""
    if rounds <= 0:
        return prompt
    text = prompt.replace(GUESS_RULE, ASK_RULE)
    rules = follow_up_rules(rounds)
    at = text.find(JSON_RULE)
    return text + rules if at < 0 else text[:at] + rules + text[at:]


def with_need(schema: Mapping[str, Any]) -> dict[str, Any]:
    """답 형식에 필수 칸 need(이름 목록)를 더한 사본. 원래 형식은 그대로 둔다."""
    out = copy.deepcopy(dict(schema))
    out["properties"] = {
        **out["properties"],
        "need": {"type": "array", "items": {"type": "string"}},
    }
    out["required"] = [*out["required"], "need"]
    return out


def need_names(data: Any) -> list[str]:
    """답의 need에서 C 이름 모양인 것만, 한 번씩, 앞에서 MAX_ASKED_NAMES개."""
    raw = data.get("need") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        name = item.strip() if isinstance(item, str) else ""
        if NEED_NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names[:MAX_ASKED_NAMES]
