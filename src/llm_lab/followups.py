"""LLM이 답의 need 칸으로 보이지 않는 정의를 되묻게 하는 규칙.

지시문에 되묻기 규칙을 붙이고, 답 형식에 need를 더하고, 답에서 이름을 꺼낸다.
"""

from __future__ import annotations

import copy
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_lab.csource import (
    ALIAS_DEPTH,
    PER_NAME,
    Definition,
    definition_rank,
    find_definitions,
    macro_target,
    scan_project,
)
from llm_lab.sources import DecodeError, SourceFile, decode_source

MAX_ASKED_NAMES = 10  # 한 번에 받는 이름 수
MAX_ASKED_DEFINITIONS = 20  # 한 번에 싣는 정의 수
MAX_ASKED_LINES = 600  # 한 번에 싣는 정의 줄 수 합
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

# 되묻기에 답하는 메시지의 문장 (fixer가 쓰고, scripts/peek_logs.py가 세는 데 쓴다)
ASKED_EDITABLE = (
    'Definitions you asked for. Those marked editable may be changed; set "file" to their path.'
)
ASKED_REFERENCE = "Definitions you asked for, for reference only."
MISSING_LINE = "Not in the project, so their behavior is unknown (external code): "
SHOWN_LINE = "Already shown above: "
LATER_LINE = "Not sent this time because of the size limit; ask again if you still need them: "
UNAVAILABLE_LINE = "Definitions cannot be looked up now."
AGAIN_LINE = "Answer again with the same JSON format. You can ask "
LAST_LINE = 'No more definitions can be sent. Answer with "need": [].'


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


def _need_name(item: Any) -> str | None:
    """need 항목 하나에서 이름. "f()", "f(int x)"는 괄호 앞, "struct cfg"·"x->y"는 마지막 이름."""
    if not isinstance(item, str):
        return None
    found = NEED_NAME.findall(item.split("(", 1)[0])
    return found[-1] if found else None


def need_names(data: Any) -> list[str]:
    """답의 need에서 꺼낸 C 이름, 한 번씩, 앞에서 MAX_ASKED_NAMES개."""
    raw = data.get("need") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        name = _need_name(item)
        if name and name not in names:
            names.append(name)
    return names[:MAX_ASKED_NAMES]


@dataclass(frozen=True)
class ProjectIndex:
    """소스 폴더 전체의 정의 (이름 → 정의 목록). 되물은 이름을 여기서 찾는다."""

    root: Path
    definitions: Mapping[str, tuple[Definition, ...]]

    @classmethod
    def build(cls, root: Path, texts: Mapping[str, str] | None = None) -> ProjectIndex:
        """root를 읽어 만든다. texts(작업이 저장한 파일 → 글)에 있는 파일은 디스크 대신
        그 글로 찾는다. 요청의 줄 번호는 작업이 저장한 글을 따르므로, 그사이 디스크 파일이
        바뀌어도 맞게 한다.
        """
        texts = texts or {}
        found, _, _ = scan_project(root)
        found = [definition for definition in found if definition.file not in texts]
        for name, text in texts.items():
            found.extend(find_definitions(name, text))
        by_name: dict[str, list[Definition]] = {}
        for definition in found:
            by_name.setdefault(definition.name, []).append(definition)
        return cls(root, {name: tuple(items) for name, items in by_name.items()})

    def source(self, name: str) -> SourceFile | None:
        """파일을 소스 폴더에서 다시 읽는다. 없거나 못 읽으면 None."""
        try:
            return decode_source(name, (self.root / name).read_bytes())
        except DecodeError, OSError:
            return None


class IndexCache:
    """작업마다 색인을 한 번만 만든다. 최근 작업 keep개만 메모리에 둔다.

    작업자 스레드 여럿이 같은 작업을 처리하므로 잠금 안에서 만든다.
    """

    def __init__(
        self,
        build: Callable[[Path, Mapping[str, str]], ProjectIndex] = ProjectIndex.build,
        keep: int = 2,
    ) -> None:
        self._build = build
        self._keep = keep
        self._lock = threading.Lock()
        self._items: OrderedDict[str, ProjectIndex | None] = OrderedDict()

    def get(
        self, job_id: str, root: str, texts: Mapping[str, str] | None = None
    ) -> ProjectIndex | None:
        """작업의 색인. 소스 폴더가 없거나 읽다 실패하면 None.

        texts는 작업이 저장한 파일의 글이다. 색인은 작업마다 처음 한 번만 만든다.
        """
        with self._lock:
            if job_id in self._items:
                self._items.move_to_end(job_id)
                return self._items[job_id]
            index = None
            if root and Path(root).is_dir():
                try:
                    index = self._build(Path(root), texts or {})
                except OSError:
                    index = None
            self._items[job_id] = index
            while len(self._items) > self._keep:
                self._items.popitem(last=False)
            return index


@dataclass(frozen=True)
class Asked:
    """되묻기 한 번에 답할 것."""

    definitions: tuple[Definition, ...] = ()  # 보낼 정의 (보낼 순서)
    missing: tuple[str, ...] = ()  # 프로젝트에 정의가 없는 이름
    shown: tuple[str, ...] = ()  # 이미 보낸 이름
    later: tuple[str, ...] = ()  # 상한으로 이번에 못 보낸 이름
    unavailable: bool = False  # 색인을 만들 수 없어 찾지 못함
    files: Mapping[str, SourceFile] = field(default_factory=dict)  # 새로 고칠 수 있게 된 파일


class _Full(Exception):
    """이번 되묻기에 실을 수 있는 정의 수·줄 수를 넘음."""


def find_asked(
    names: list[str],
    index: ProjectIndex | None,
    source_name: str,
    first: int,
    last: int,
    sent: Iterable[Definition],
) -> Asked:
    """되물은 이름의 정의를 색인에서 고른다.

    이름마다 검사 파일 → 헤더 → 그 밖 순서로 PER_NAME개, 별칭 매크로는 가리키는 구현까지
    (ALIAS_DEPTH 단계). 보낸 코드 범위(source_name의 first~last) 안의 것과 sent에 있는 몸체는
    싣지 않는다. 새로 실은 것이 없으면 그 이름은 shown이다.
    """
    if index is None:
        return Asked(unavailable=True)
    placed = {(definition.file, definition.first) for definition in sent}
    picked: list[Definition] = []
    total = 0

    def place(name: str, depth: int) -> bool:
        """name의 정의를 싣는다. 새로 실은 것이 있으면 True. 상한을 넘으면 _Full."""
        nonlocal total
        added = False
        candidates = sorted(
            index.definitions.get(name, ()),
            # 같은 이름이면 몸체를 원형(declaration)보다 먼저
            key=lambda d: (
                d.kind == "declaration",
                definition_rank(d, source_name),
                d.file,
                d.first,
            ),
        )
        for definition in candidates[:PER_NAME]:
            key = (definition.file, definition.first)
            inside = (
                definition.file == source_name
                and first <= definition.first
                and definition.last <= last
            )
            if not inside and key not in placed:
                size = definition.text.count("\n") + 1
                if len(picked) >= MAX_ASKED_DEFINITIONS or total + size > MAX_ASKED_LINES:
                    raise _Full
                picked.append(definition)
                placed.add(key)
                total += size
                added = True
            # 별칭은 이미 보냈어도 가리키는 구현은 따라간다
            target = macro_target(definition)
            if target and depth < ALIAS_DEPTH:
                added = place(target, depth + 1) or added
        return added

    missing: list[str] = []
    shown: list[str] = []
    later: list[str] = []
    full = False
    for name in names:
        if name not in index.definitions:
            missing.append(name)
        elif full:
            later.append(name)
        else:
            try:
                if not place(name, 0):
                    shown.append(name)
            except _Full:
                full = True
                later.append(name)
    return Asked(tuple(picked), tuple(missing), tuple(shown), tuple(later))
