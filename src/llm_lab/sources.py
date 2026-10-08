"""소스 파일 디코딩과 RTE 행 ↔ 소스 짝짓기.

소스는 사용자가 지정한 폴더에서 찾는다. 짝짓기(locate_files)는 폴더 기준 상대 경로 목록만
받으므로, 나중에 브라우저에서 폴더를 통째로 올리는 방식을 붙여도 그대로 쓸 수 있다.
"""

from __future__ import annotations

import codecs
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class DecodeError(Exception):
    """UTF-8도 CP949도 아닌 파일, 또는 줄바꿈이 섞인 파일."""


@dataclass(frozen=True)
class SourceFile:
    name: str  # 소스 폴더 기준 상대 경로 (/ 구분). 패치 안의 경로가 된다
    text: str  # 줄바꿈을 \n으로 통일한 내용 (BOM 제외)
    encoding: str  # "utf-8" | "cp949"
    newline: str  # "\r\n" | "\n"
    bom: bool
    sha256: str  # 원본 바이트의 해시 (캐시 키에 쓴다)


@dataclass(frozen=True)
class Match:
    source: SourceFile | None
    reason: str = ""  # source가 None일 때 이유


@dataclass(frozen=True)
class Located:
    path: str | None  # 소스 폴더 기준 상대 경로 (/ 구분)
    reason: str = ""  # path가 None일 때 이유


def base_name(path: str) -> str:
    """Windows·POSIX 경로 모두에서 마지막 부분(파일 이름)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def decode_source(name: str, data: bytes) -> SourceFile:
    sha256 = hashlib.sha256(data).hexdigest()
    bom = data.startswith(codecs.BOM_UTF8)
    try:
        text, encoding = data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        try:
            # 한글 주석이 든 오래된 C 파일은 CP949인 경우가 많다
            text, encoding = data.decode("cp949"), "cp949"
        except UnicodeDecodeError:
            raise DecodeError("인코딩 미지원 (UTF-8·CP949가 아님)") from None
    crlf = text.count("\r\n")
    if 0 < crlf < text.count("\n"):
        # 줄마다 줄바꿈이 다르면 패치의 원본 줄과 바이트가 어긋나 git apply가 거부한다
        raise DecodeError("줄바꿈이 CRLF와 LF로 섞여 있음 - 한 가지로 맞춘 뒤 올리세요")
    newline = "\r\n" if crlf else "\n"
    return SourceFile(
        name=name,
        text=text.replace("\r\n", "\n"),
        encoding=encoding,
        newline=newline,
        bom=bom,
        sha256=sha256,
    )


def _common_tail(a: list[str], b: list[str]) -> int:
    """두 경로의 끝에서부터 같은 부분이 몇 개인지 (대소문자 무시)."""
    count = 0
    for x, y in zip(reversed(a), reversed(b), strict=False):
        if x.casefold() != y.casefold():
            break
        count += 1
    return count


def locate_files(excel_paths: Iterable[str], candidates: Iterable[str]) -> dict[str, Located]:
    """엑셀 File 경로마다 후보(폴더 기준 상대 경로) 중 하나를 고른다.

    파일 이름이 같은 후보 중에서 엑셀 경로와 끝부분(폴더 이름들)이 가장 길게 겹치는 것을 고른다.
    대소문자는 무시한다(Windows). 하나로 정해지지 않으면 이유와 함께 None.
    """
    by_name: dict[str, list[list[str]]] = {}
    for candidate in candidates:
        parts = candidate.replace("\\", "/").split("/")
        by_name.setdefault(parts[-1].casefold(), []).append(parts)
    found: dict[str, Located] = {}
    for excel_path in sorted(set(excel_paths)):
        parts = [part for part in excel_path.replace("\\", "/").split("/") if part]
        if not parts:
            found[excel_path] = Located(None, "File 칸이 비어 있음")
            continue
        same_name = by_name.get(parts[-1].casefold(), [])
        if not same_name:
            found[excel_path] = Located(None, f"폴더에 {parts[-1]}이(가) 없음")
            continue
        scores = [(_common_tail(parts, candidate), "/".join(candidate)) for candidate in same_name]
        best = max(score for score, _ in scores)
        winners = sorted(path for score, path in scores if score == best)
        if len(winners) > 1:
            reason = f"폴더에 같은 이름의 파일이 여러 개라 고를 수 없음: {', '.join(winners)}"
            found[excel_path] = Located(None, reason)
        else:
            found[excel_path] = Located(winners[0])
    return found


def list_files(root: Path, names: set[str]) -> list[str]:
    """root 아래에서 이름(소문자)이 names에 든 파일의 상대 경로.

    .git처럼 점으로 시작하는 폴더는 건너뛴다.
    """
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name.casefold() in names:
                found.append(Path(folder, name).relative_to(root).as_posix())
    return found


def load_from_folder(
    root: Path, excel_paths: Iterable[str]
) -> tuple[dict[str, Match], list[SourceFile]]:
    """엑셀에 나온 파일만 root에서 찾아 읽는다. (엑셀 경로 → 짝, 읽은 소스 목록)."""
    paths = sorted(set(excel_paths))
    names = {base_name(path).casefold() for path in paths if base_name(path)}
    located = locate_files(paths, list_files(root, names))
    sources: dict[str, SourceFile] = {}
    failures: dict[str, str] = {}
    matches: dict[str, Match] = {}
    for excel_path, where in located.items():
        relative = where.path
        if relative is None:
            matches[excel_path] = Match(None, where.reason)
            continue
        if relative not in sources and relative not in failures:
            try:
                sources[relative] = decode_source(relative, (root / relative).read_bytes())
            except DecodeError as exc:
                failures[relative] = f"{relative}: {exc}"
            except OSError as exc:
                failures[relative] = f"{relative}을(를) 읽을 수 없음: {exc}"
        if relative in failures:
            matches[excel_path] = Match(None, failures[relative])
        else:
            matches[excel_path] = Match(sources[relative])
    return matches, list(sources.values())
