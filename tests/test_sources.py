import codecs

import pytest

from llm_lab.sources import DecodeError, base_name, decode_source, match_sources


def test_decodes_utf8_lf():
    source = decode_source("a.c", b"int x;\nint y;\n")

    assert (source.text, source.encoding, source.newline, source.bom) == (
        "int x;\nint y;\n",
        "utf-8",
        "\n",
        False,
    )
    assert len(source.sha256) == 64


def test_decodes_utf8_bom_crlf_and_normalizes_newlines():
    source = decode_source("a.c", codecs.BOM_UTF8 + b"int x;\r\nint y;\r\n")

    assert source.text == "int x;\nint y;\n"
    assert (source.encoding, source.newline, source.bom) == ("utf-8", "\r\n", True)


def test_falls_back_to_cp949_for_korean_comments():
    source = decode_source("a.c", "/* 합계 */\r\nint x;\r\n".encode("cp949"))

    assert source.text == "/* 합계 */\nint x;\n"
    assert (source.encoding, source.newline) == ("cp949", "\r\n")


def test_rejects_unknown_encoding():
    with pytest.raises(DecodeError, match="인코딩 미지원"):
        decode_source("a.c", b"\xff\xfe\xfa\x00")


def test_rejects_mixed_line_endings():
    # 한 줄만 LF인 CRLF 파일: 패치의 원본 줄 바이트가 어긋나므로 받지 않는다
    with pytest.raises(DecodeError, match="CRLF와 LF로 섞여"):
        decode_source("a.c", b"int a;\r\nint b;\nint c;\r\n")


def test_upload_name_keeps_only_file_name():
    assert decode_source(r"C:\x\a.c", b"").name == "a.c"
    assert base_name("src/lib/b.c") == "b.c"


def test_matches_by_file_name_ignoring_case():
    calc = decode_source("Calc.C", b"int x;\n")

    matches = match_sources([r"C:\proj\src\calc.c"], [calc])

    assert matches[r"C:\proj\src\calc.c"].source is calc


def test_missing_ambiguous_and_undecodable_sources_explain_why():
    a1 = decode_source("a.c", b"1\n")
    a2 = decode_source("a.c", b"2\n")
    paths = [
        r"C:\p\src\a.c",
        r"C:\p\src\missing.c",
        r"C:\p\x\util.c",
        r"C:\p\y\util.c",
        r"C:\p\src\bad.c",
        "",
    ]

    matches = match_sources(paths, [a1, a2], undecodable={"bad.c": "인코딩 미지원"})

    assert "같은 이름의 업로드 파일이 2개" in matches[r"C:\p\src\a.c"].reason
    assert "missing.c" in matches[r"C:\p\src\missing.c"].reason
    assert "C:/p/x/util.c, C:/p/y/util.c" in matches[r"C:\p\x\util.c"].reason
    assert "인코딩" in matches[r"C:\p\src\bad.c"].reason
    assert matches[""].reason == "File 칸이 비어 있음"
    assert all(match.source is None for match in matches.values())
