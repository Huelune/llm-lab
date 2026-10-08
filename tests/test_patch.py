import codecs
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_lab.fixer import Edit, apply_edits, locate_edits
from llm_lab.patch import (
    APPLY_COMMAND,
    FilePatch,
    PatchConflict,
    base_folder,
    build_patch,
    find_conflicts,
    pick_without_conflicts,
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


def test_base_folder_on_different_drives_is_unknown():
    assert base_folder([r"C:\proj\a.c", r"D:\proj\b.c"]) is None


def test_base_folder_ignores_letter_case_like_windows():
    paths = [r"C:\Proj\src\a.c", r"c:\proj\lib\b.c"]

    base = base_folder(paths)

    assert base == "C:/Proj"
    assert [relative_path(path, base) for path in paths] == ["src/a.c", "lib/b.c"]


def test_base_folder_of_posix_absolute_paths():
    paths = ["/proj/a.c", "/work/b.c"]

    assert base_folder(paths) == "/"
    assert [relative_path(path, "/") for path in paths] == ["proj/a.c", "work/b.c"]


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


def test_fixes_for_neighbouring_lines_no_longer_conflict():
    # 두 행의 LLM 수정이 같은 두 줄을 통째로 보냈어도 실제로 바꾼 줄은 다르다
    text = "void f(void)\n{\n    a = a + 1;\n    b = b + 1;\n}\n"
    block = "    a = a + 1;\n    b = b + 1;\n"
    first = locate_edits(
        text, 1, 5, [{"original": block, "replacement": block.replace("a = a + 1", "a = inc(a)")}]
    )
    second = locate_edits(
        text, 1, 5, [{"original": block, "replacement": block.replace("b = b + 1", "b = inc(b)")}]
    )
    source = decode_source("f.c", text.encode())
    edits = [(3, edit) for edit in first] + [(4, edit) for edit in second]

    patch = build_patch([FilePatch("f.c", source, edits)])

    assert b"+    a = inc(a);\n" in patch
    assert b"+    b = inc(b);\n" in patch


def test_edits_conflict_only_when_they_touch_the_same_text():
    a, b, c = Edit(10, 20, "x"), Edit(15, 25, "y"), Edit(25, 30, "z")
    insert, other_insert, inside = Edit(5, 5, "i"), Edit(5, 5, "j"), Edit(12, 12, "k")
    rows = {
        1: ("a.c", [a]),
        2: ("a.c", [b]),
        3: ("a.c", [c]),  # b가 끝나는 자리에서 시작: 겹치지 않음
        4: ("a.c", [insert]),
        5: ("a.c", [other_insert]),  # 같은 자리에 다른 내용을 끼워 넣음: 순서를 정할 수 없음
        6: ("a.c", [inside]),
        7: ("b.c", [b]),  # 다른 파일
        8: ("a.c", [a]),  # 행 1과 똑같은 수정: 하나로 합쳐지므로 충돌 아님
    }

    assert find_conflicts(rows) == {
        1: {2, 6},
        2: {1, 8},
        3: set(),
        4: {5},
        5: {4},
        6: {1, 8},
        7: set(),
        8: {2, 6},
    }


def test_pick_without_conflicts_follows_the_given_order():
    conflicts = {1: {2}, 2: {1, 3}, 3: {2}, 4: set()}

    assert pick_without_conflicts([2, 1, 3, 4], conflicts) == {2, 4}
    assert pick_without_conflicts([1, 2, 3, 4], conflicts) == {1, 3, 4}


def test_insert_at_start_of_replaced_text_goes_before_the_replacement():
    assert apply_edits("abc", [Edit(1, 1, "X"), Edit(1, 2, "Y")]) == "aXYc"


def test_conflict_message_is_short_and_says_what_to_do():
    message = str(PatchConflict([(n, n + 1) for n in range(10)]))

    assert "행 0와 행 1, 행 1와 행 2, 행 2와 행 3 외 7쌍" in message
    assert "수정 모두 선택" in message
