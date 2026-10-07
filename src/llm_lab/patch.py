"""고른 수정들을 git apply로 적용할 수 있는 패치 파일로 만든다."""

from __future__ import annotations

import difflib
import posixpath
from collections.abc import Iterable
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
        listed = ", ".join(f"행 {a}와 행 {b}" for a, b in pairs)
        super().__init__(f"같은 곳을 고치는 수정을 함께 골랐습니다: {listed}. 하나만 고르세요.")
        self.pairs = pairs


@dataclass(frozen=True)
class FilePatch:
    path: str  # 패치 안의 경로 (기준 폴더에서 본 상대 경로, / 구분)
    source: SourceFile
    edits: list[tuple[int, Edit]]  # (엑셀 행 번호, 수정)


def base_folder(excel_paths: Iterable[str]) -> str | None:
    """짝지은 파일들의 공통 부모 폴더. 파일 이름만 있는 경로가 섞이면 None."""
    parents = []
    for path in excel_paths:
        normalized = path.replace("\\", "/")
        if "/" not in normalized:
            return None
        parents.append(normalized.rsplit("/", 1)[0])
    if not parents:
        return None
    try:
        return posixpath.commonpath(parents)
    except ValueError:  # 절대·상대 경로가 섞임
        return None


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


def build_patch(files: list[FilePatch]) -> bytes:
    unique = {item.path: _unique(item.edits) for item in files}
    conflicts: list[tuple[int, int]] = []
    for edits in unique.values():
        reach_row, reach_end = 0, -1  # 지금까지 가장 뒤까지 고치는 수정
        for row, edit in sorted(edits, key=lambda pair: pair[1].start):
            if edit.start < reach_end and (reach_row, row) not in conflicts:
                conflicts.append((reach_row, row))
            if edit.end > reach_end:
                reach_row, reach_end = row, edit.end
    if conflicts:
        raise PatchConflict(conflicts)
    chunks = []
    for item in sorted(files, key=lambda item: item.path):
        fixed = apply_edits(item.source.text, [edit for _, edit in unique[item.path]])
        chunks.append(_file_diff(item, fixed))
    return b"".join(chunks)
