import codecs

import pytest

from llm_lab.sources import (
    DecodeError,
    base_name,
    decode_source,
    load_from_folder,
    locate_files,
)


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


def test_decode_keeps_the_given_name():
    # 폴더에서 읽은 파일은 폴더 기준 상대 경로를 이름으로 쓴다 (패치 경로가 된다)
    assert decode_source("src/lib/b.c", b"").name == "src/lib/b.c"
    assert base_name(r"C:\x\src\b.c") == "b.c"


def test_locate_picks_file_by_name_ignoring_case():
    found = locate_files([r"C:\proj\src\Calc.C"], ["src/calc.c", "doc/readme.txt"])

    assert found[r"C:\proj\src\Calc.C"].path == "src/calc.c"


def test_locate_prefers_longest_matching_folder_tail():
    candidates = ["x/util.c", "y/util.c", "y/z/util.c"]

    found = locate_files([r"C:\old\proj\x\util.c", r"C:\old\proj\y\util.c"], candidates)

    assert found[r"C:\old\proj\x\util.c"].path == "x/util.c"
    assert found[r"C:\old\proj\y\util.c"].path == "y/util.c"


def test_locate_explains_missing_and_ambiguous_files():
    found = locate_files([r"C:\p\q\util.c", r"C:\p\missing.c", ""], ["x/util.c", "y/util.c"])

    assert found[r"C:\p\q\util.c"].path is None
    assert "x/util.c, y/util.c" in found[r"C:\p\q\util.c"].reason
    assert found[r"C:\p\missing.c"].reason == "폴더에 missing.c이(가) 없음"
    assert found[""].reason == "File 칸이 비어 있음"


def test_load_from_folder_reads_only_referenced_files(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "calc.c").write_bytes(b"int x;\n")
    (root / ".git" / "calc.c").write_bytes(b"old copy\n")  # 점으로 시작하는 폴더는 보지 않는다
    (root / "src" / "bad.c").write_bytes(b"int a;\r\nint b;\n")
    paths = [r"C:\proj\src\calc.c", r"C:\proj\src\bad.c", r"C:\proj\src\none.c"]

    matches, sources = load_from_folder(root, paths)

    assert [source.name for source in sources] == ["src/calc.c"]
    assert matches[r"C:\proj\src\calc.c"].source is sources[0]
    assert "src/bad.c: 줄바꿈이 CRLF와 LF로 섞여" in matches[r"C:\proj\src\bad.c"].reason
    assert matches[r"C:\proj\src\none.c"].reason == "폴더에 none.c이(가) 없음"
