"""업로드한 소스 파일 디코딩과 RTE 행 ↔ 소스 짝짓기."""

from __future__ import annotations

import codecs
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class DecodeError(Exception):
    """UTF-8도 CP949도 아닌 파일."""


@dataclass(frozen=True)
class SourceFile:
    name: str  # 업로드 이름 (파일 이름만)
    text: str  # 줄바꿈을 \n으로 통일한 내용 (BOM 제외)
    encoding: str  # "utf-8" | "cp949"
    newline: str  # "\r\n" | "\n"
    bom: bool
    sha256: str  # 원본 바이트의 해시 (캐시 키에 쓴다)


@dataclass(frozen=True)
class Match:
    source: SourceFile | None
    reason: str = ""  # source가 None일 때 이유


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
        name=base_name(name),
        text=text.replace("\r\n", "\n"),
        encoding=encoding,
        newline=newline,
        bom=bom,
        sha256=sha256,
    )


def match_sources(
    excel_paths: Iterable[str],
    sources: list[SourceFile],
    undecodable: Mapping[str, str] | None = None,
) -> dict[str, Match]:
    """엑셀 File 경로마다 업로드 소스를 이름으로 짝짓는다 (대소문자 무시).

    브라우저 업로드는 폴더 없이 파일 이름만 보내므로 이름으로만 짝지을 수 있다.
    """
    paths = sorted(set(excel_paths))
    paths_by_name: dict[str, set[str]] = {}
    for path in paths:
        normalized = path.replace("\\", "/")
        paths_by_name.setdefault(base_name(path).casefold(), set()).add(normalized)
    uploads: dict[str, list[SourceFile]] = {}
    for source in sources:
        uploads.setdefault(source.name.casefold(), []).append(source)
    # 읽지 못한 업로드 파일 이름 → 이유
    bad = {base_name(name).casefold(): reason for name, reason in (undecodable or {}).items()}

    matches: dict[str, Match] = {}
    for path in paths:
        key = base_name(path).casefold()
        found = uploads.get(key, [])
        if not base_name(path):
            matches[path] = Match(None, "File 칸이 비어 있음")
        elif len(paths_by_name[key]) > 1:
            others = ", ".join(sorted(paths_by_name[key]))
            matches[path] = Match(None, f"엑셀에 같은 이름의 파일이 여러 경로로 나옴: {others}")
        elif key in bad:
            matches[path] = Match(None, bad[key])
        elif not found:
            matches[path] = Match(None, f"업로드한 소스에 {base_name(path)}이(가) 없음")
        elif len(found) > 1:
            matches[path] = Match(None, f"같은 이름의 업로드 파일이 {len(found)}개")
        else:
            matches[path] = Match(found[0])
    return matches
