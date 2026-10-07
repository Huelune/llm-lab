import codecs
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_lab.fixer import Edit
from llm_lab.patch import (
    APPLY_COMMAND,
    FilePatch,
    PatchConflict,
    base_folder,
    build_patch,
    relative_path,
)
from llm_lab.sources import decode_source

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git 없음")


def replace(source, old: str, new: str) -> Edit:
    start = source.text.index(old)
    return Edit(start, start + len(old), new)


def git_apply(folder: Path, patch: bytes) -> None:
    """결과 화면이 안내하는 명령 그대로 적용한다."""
    (folder / "fixes.patch").write_bytes(patch)
    for args in (["--check"], []):
        subprocess.run(
            [*APPLY_COMMAND.split(), *args, "fixes.patch"],
            cwd=folder,
            check=True,
            capture_output=True,
        )


def test_base_folder_and_relative_paths():
    paths = [r"C:\proj\src\a.c", r"C:\proj\lib\b.c"]

    base = base_folder(paths)

    assert base == "C:/proj"
    assert [relative_path(path, base) for path in paths] == ["src/a.c", "lib/b.c"]
    assert base_folder([r"C:\proj\src\a.c"]) == "C:/proj/src"
    assert base_folder(["a.c", r"C:\proj\b.c"]) is None
    assert relative_path(r"C:\proj\b.c", None) == "b.c"
    assert base_folder(["src/a.c", "lib/b.c"]) == ""
    assert relative_path("src/a.c", "") == "src/a.c"


def test_patch_for_one_lf_file():
    source = decode_source("a.c", b"int a;\nint b;\nint c;\n")
    item = FilePatch("src/a.c", source, [(2, replace(source, "int b;", "long b;"))])

    patch = build_patch([item]).decode()

    assert patch == (
        "--- a/src/a.c\n+++ b/src/a.c\n@@ -1,3 +1,3 @@\n int a;\n-int b;\n+long b;\n int c;\n"
    )


def test_overlapping_edits_from_different_rows_conflict():
    source = decode_source("a.c", b"int a;\nint b;\n")
    edits = [
        (2, Edit(0, 13, "x")),
        (5, replace(source, "int a;", "y")),
        (7, replace(source, "int b;", "z")),
    ]

    with pytest.raises(PatchConflict) as caught:
        build_patch([FilePatch("a.c", source, edits)])

    assert caught.value.pairs == [(2, 5), (2, 7)]
    assert "행 2와 행 5" in str(caught.value)


@needs_git
@pytest.mark.parametrize(
    "original",
    [
        b"int a;\nint b;\nint c;\n",
        "/* 합계 */\r\nint a;\r\nint b;\r\nint c;\r\n".encode("cp949"),
        codecs.BOM_UTF8 + b"int b;\r\nint c;\r\n",
        b"int a;\nint c;\nint b;",
    ],
    ids=["utf8-lf", "cp949-crlf", "bom-crlf", "no-final-newline"],
)
def test_git_apply_reproduces_the_fix_byte_for_byte(tmp_path, original):
    source = decode_source("a.c", original)
    item = FilePatch("src/a.c", source, [(2, replace(source, "int b;", "long b; /* 수정 */"))])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.c").write_bytes(original)

    git_apply(tmp_path, build_patch([item]))

    expected = original.replace(b"int b;", "long b; /* 수정 */".encode(source.encoding))
    assert (tmp_path / "src" / "a.c").read_bytes() == expected


def test_identical_edits_from_two_rows_are_applied_once():
    source = decode_source("a.c", b"int a;\nint b;\n")
    edit = replace(source, "int a;", "long a;")

    patch = build_patch([FilePatch("a.c", source, [(2, edit), (5, edit)])]).decode()

    assert patch.count("+long a;") == 1


@needs_git
def test_git_apply_with_korean_folder_name(tmp_path):
    source = decode_source("a.c", b"int a;\n")
    item = FilePatch("소스/a.c", source, [(2, replace(source, "int a;", "long a;"))])
    (tmp_path / "소스").mkdir()
    (tmp_path / "소스" / "a.c").write_bytes(b"int a;\n")

    git_apply(tmp_path, build_patch([item]))

    assert (tmp_path / "소스" / "a.c").read_bytes() == b"long a;\n"


@needs_git
def test_git_apply_two_files_and_two_edits_in_one_file(tmp_path):
    a = decode_source("a.c", b"int a;\n" + b"int x;\n" * 20 + b"int z;\n")
    b = decode_source("b.c", b"int b;\n")
    items = [
        FilePatch(
            "src/a.c",
            a,
            [(3, replace(a, "int z;", "long z;")), (2, replace(a, "int a;", "long a;"))],
        ),
        FilePatch("lib/b.c", b, [(4, replace(b, "int b;", "long b;"))]),
    ]
    for path, source in (("src/a.c", a), ("lib/b.c", b)):
        (tmp_path / path).parent.mkdir(exist_ok=True)
        (tmp_path / path).write_text(source.text, newline="\n")

    git_apply(tmp_path, build_patch(items))

    assert (tmp_path / "src/a.c").read_text().startswith("long a;\n")
    assert (tmp_path / "src/a.c").read_text().endswith("long z;\n")
    assert (tmp_path / "lib/b.c").read_text() == "long b;\n"
