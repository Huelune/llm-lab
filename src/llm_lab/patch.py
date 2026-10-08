"""고른 수정들을 git apply로 적용할 수 있는 패치 파일로 만든다."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from llm_lab.fixer import Edit, apply_edits
from llm_lab.sources import SourceFile, base_name

# Git for Windows 기본 설정(core.autocrlf=true)은 git apply가 쓰는 파일의 LF를 CRLF로 바꾼다.
# 패치가 파일마다 원래 줄바꿈을 담고 있으므로 변환을 끄고 바이트 그대로 적용하게 한다.
APPLY_COMMAND = "git -c core.autocrlf=false apply"


class PatchError(Exception):
    """패치를 만들 수 없음."""


class PatchConflict(PatchError):
    """고른 수정끼리 같은 곳을 고침."""

    def __init__(self, pairs: list[tuple[int, int]]) -> None:
        listed = ", ".join(f"행 {a}와 행 {b}" for a, b in pairs[:3])
        more = f" 외 {len(pairs) - 3}쌍" if len(pairs) > 3 else ""
        super().__init__(
            f"같은 줄을 고치는 수정이 함께 골라졌습니다: {listed}{more}. "
            "결과 화면에서 '수정 모두 선택'을 다시 누르면 겹치지 않게 골라집니다."
        )
        self.pairs = pairs


@dataclass(frozen=True)
class FilePatch:
    path: str  # 패치 안의 경로 (기준 폴더에서 본 상대 경로, / 구분)
    source: SourceFile
    edits: list[tuple[int, Edit]]  # (엑셀 행 번호, 수정)


def base_folder(excel_paths: Iterable[str]) -> str | None:
    """짝지은 파일들의 공통 부모 폴더 (Windows처럼 대소문자 무시, 표기는 첫 경로 기준).

    파일 이름만 있는 경로가 섞였거나 드라이브가 달라 공통 폴더가 없으면 None.
    """
    parents = []
    for path in excel_paths:
        normalized = path.replace("\\", "/")
        if "/" not in normalized:
            return None
        parents.append(normalized.rsplit("/", 1)[0].split("/"))
    if not parents:
        return None
    common = []
    for parts in zip(*parents, strict=False):
        if len({part.casefold() for part in parts}) > 1:
            break
        common.append(parts[0])
    if common == [""]:
        return "/"
    if not common and any(_absolute(parts) for parts in parents):
        return None  # 드라이브가 다르거나 절대·상대 경로가 섞임
    return "/".join(common)


def _absolute(parts: list[str]) -> bool:
    return parts[0] == "" or bool(re.fullmatch(r"[A-Za-z]:", parts[0]))


def relative_path(excel_path: str, base: str | None) -> str:
    normalized = excel_path.replace("\\", "/")
    if base is None:
        return base_name(normalized)
    return normalized[len(base) :].lstrip("/") if base else normalized


def _keep_ends(text: str, newline: str) -> list[str]:
    """줄바꿈을 붙인 채로 \\n에서만 나누고, 줄바꿈을 파일의 원래 형식으로 되돌린다."""
    lines = text.split("\n")
    kept = [line + newline for line in lines[:-1]]
    if lines[-1]:
        kept.append(lines[-1])
    return kept


def _file_diff(item: FilePatch, fixed: str) -> bytes:
    source = item.source
    bom = "﻿" if source.bom else ""
    old = _keep_ends(bom + source.text, source.newline)
    new = _keep_ends(bom + fixed, source.newline)
    out = bytearray()
    for number, line in enumerate(
        difflib.unified_diff(old, new, f"a/{item.path}", f"b/{item.path}", n=3)
    ):
        if number < 2 or line.startswith("@@"):
            out += line.encode("utf-8")  # 헤더: 경로는 UTF-8 (git의 경로 표기)
            continue
        try:
            out += line.encode(source.encoding)
        except UnicodeEncodeError as exc:
            raise PatchError(
                f"{item.path}: 수정 내용에 {source.encoding}로 쓸 수 없는 문자가 있음"
            ) from exc
        if not line.endswith("\n"):
            out += b"\n\\ No newline at end of file\n"
    return bytes(out)


def _unique(edits: list[tuple[int, Edit]]) -> list[tuple[int, Edit]]:
    """두 행이 똑같은 수정을 냈으면 하나만 남긴다 (먼저 나온 행 번호 기준)."""
    first_row: dict[Edit, int] = {}
    for row, edit in edits:
        first_row.setdefault(edit, row)
    return [(row, edit) for edit, row in first_row.items()]


def _conflict(a: Edit, b: Edit) -> bool:
    """두 수정을 함께 적용할 수 없는지. 똑같은 수정은 하나로 합치므로 충돌이 아니다."""
    if a == b:
        return False
    if a.start == a.end == b.start == b.end:
        return True  # 같은 자리에 서로 다른 내용을 끼워 넣으면 순서를 정할 수 없다
    return a.start < b.end and b.start < a.end


def find_conflicts(rows: Mapping[int, tuple[str, Sequence[Edit]]]) -> dict[int, set[int]]:
    """행 → 함께 고를 수 없는 행들. rows는 행 → (파일, 그 행의 수정들)."""
    conflicts: dict[int, set[int]] = {row: set() for row in rows}
    items = list(rows.items())
    for index, (row_a, (file_a, edits_a)) in enumerate(items):
        for row_b, (file_b, edits_b) in items[index + 1 :]:
            if file_a == file_b and any(_conflict(a, b) for a in edits_a for b in edits_b):
                conflicts[row_a].add(row_b)
                conflicts[row_b].add(row_a)
    return conflicts


def pick_without_conflicts(order: Iterable[int], conflicts: Mapping[int, set[int]]) -> set[int]:
    """order 순서대로, 이미 고른 행과 겹치지 않는 행만 고른다."""
    picked: set[int] = set()
    for row in order:
        if not conflicts.get(row, set()) & picked:
            picked.add(row)
    return picked


def build_patch(files: list[FilePatch]) -> bytes:
    unique = {item.path: _unique(item.edits) for item in files}
    rows: dict[int, tuple[str, list[Edit]]] = {}
    for path, edits in unique.items():
        for row, edit in edits:
            rows.setdefault(row, (path, []))[1].append(edit)
    conflicts = find_conflicts(rows)
    pairs = sorted({(min(a, b), max(a, b)) for a, partners in conflicts.items() for b in partners})
    if pairs:
        raise PatchConflict(pairs)
    chunks = []
    for item in sorted(files, key=lambda item: item.path):
        fixed = apply_edits(item.source.text, [edit for _, edit in unique[item.path]])
        chunks.append(_file_diff(item, fixed))
    return b"".join(chunks)
