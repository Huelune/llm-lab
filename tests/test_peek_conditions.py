"""scripts/peek_conditions.py: 조건 시트 요약이 한 화면에 맞고, 회사 정보를 찍지 않는지."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from openpyxl import Workbook

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peek_conditions.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peek_conditions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


peek = _load()

CATEGORIES = ["Mandatory", "Required", "Advisory"]


def _workbook(path: Path, misra_rules: int = 5) -> Path:
    book = Workbook()
    book.active.title = "Cover"
    book.active.append(["Polyspace 보고서"])
    for title in ("MISRA_Rule_Result", "CodeMetrics_Result", "RTE_Result"):
        book.create_sheet(title).append(["File", "Function", "line", "Check"])
    misra = book.create_sheet("MISRA_C_2012_Rule")
    misra.append(["MISRA C:2012 Rules"])  # 제목 행
    misra.append([])
    misra.append(["Rule", "Category", "Applied", "Description"])
    for i in range(misra_rules):
        misra.append(
            [f"{i}.1", CATEGORIES[i % 3], "Yes" if i % 4 else "No", f"규칙 본문 {i} " * 20]
        )
    metrics = book.create_sheet("Code Metrics")
    metrics.append(["Metric", "Threshold", "Direction"])
    metrics.append(["Cyclomatic Complexity", 10, "max"])
    metrics.append(["Comment Density", "<= 20", "min"])
    book.create_sheet("RTE").append(["Check", "Target"])
    book["RTE"].append(["Overflow", "Red, Orange"])
    book.save(path)
    return path


def _block(screen: list[str], title: str) -> list[str]:
    """[title] 줄부터 다음 [시트] 줄 앞까지."""
    start = next(i for i, line in enumerate(screen) if line.startswith(f"[{title}]"))
    end = next((i for i in range(start + 1, len(screen)) if screen[i].startswith("[")), len(screen))
    return screen[start:end]


def test_finds_condition_sheets_only(tmp_path: Path) -> None:
    screen = peek.summarize(_workbook(tmp_path / "p.xlsx"))
    text = "\n".join(screen)

    assert screen[0] == "조건 시트 3개 / 그 밖의 시트 4개 (이름은 찍지 않음)"
    assert all(f"[{t}]" in text for t in ("MISRA_C_2012_Rule", "Code Metrics", "RTE"))
    assert "Cover" not in text and "_Result" not in text and "p.xlsx" not in text


def test_small_sheet_shows_every_row_with_row_numbers(tmp_path: Path) -> None:
    screen = peek.summarize(_workbook(tmp_path / "p.xlsx"))
    block = _block(screen, "Code Metrics")

    assert "3행 중 앞 3행" in block[0] and "헤더 추정 1행 / 열 3개" in block[0]
    # 표준 용어가 아닌 머리(Direction)는 길이만
    assert block[1].split() == ["1:", "Metric", "|", "Threshold", "|", "<글", "9자>"]
    assert block[2].split() == ["2:", "Cyclomatic", "Complexity", "|", "10", "|", "max"]
    assert block[3].strip() == "3: Comment Density | <= 20 | min"


def test_kinds_come_from_rows_under_the_guessed_header(tmp_path: Path) -> None:
    screen = peek.summarize(_workbook(tmp_path / "p.xlsx", misra_rules=60))
    block = _block(screen, "MISRA_C_2012_Rule")

    # 제목 1 + 헤더 1 + 규칙 60. 빈 행은 세지 않는다. 다 안 들어가니 값 종류 줄이 붙는다
    assert "62행 중" in block[0] and "헤더 추정 3행 / 열 4개" in block[0]
    assert any("Applied: Yes 45, No 15" in line for line in block)
    assert any("Category: Mandatory 20, Required 20, Advisory 20" in line for line in block)
    # 표준 용어가 아닌 글은 길이만
    assert any(line.strip().startswith("4: 0.1 | Mandatory | No | <글 ") for line in block)
    assert "설명" not in "\n".join(block)


def test_big_sheet_fits_one_screen(tmp_path: Path) -> None:
    screen = peek.summarize(_workbook(tmp_path / "p.xlsx", misra_rules=300))

    assert len(screen) <= peek.SCREEN_LINES - 1  # 마지막 한 줄은 프롬프트 몫
    assert all(peek.width(line) <= peek.WIDTH for line in screen)
    # 작은 시트는 다 나오고 남은 줄은 큰 시트가 쓴다
    assert len(_block(screen, "Code Metrics")) == 1 + 3
    assert "302행 중 앞" in _block(screen, "MISRA_C_2012_Rule")[0]


def test_many_condition_sheets_still_fit(tmp_path: Path) -> None:
    book = Workbook()
    book.active.title = "Cover"
    for n in range(12):
        sheet = book.create_sheet(f"MISRA part {n}")
        sheet.append(["Rule", "Category"])
        for i in range(20):
            sheet.append([f"{n}.{i}", CATEGORIES[i % 3]])
    path = tmp_path / "many.xlsx"
    book.save(path)

    screen = peek.summarize(path)

    assert len(screen) <= peek.SCREEN_LINES - 1
    assert "조건 시트 12개" in screen[0]


def test_missing_sheets_lists_only_safe_titles(tmp_path: Path) -> None:
    book = Workbook()
    book.active.title = "Cover"
    book.active.append(["Polyspace", "보고서", "표지"])
    book.create_sheet("RTE_Result").append(["ID", "TYPE", "File"])
    book.create_sheet("secret_plan")
    path = tmp_path / "none.xlsx"
    book.save(path)

    text = "\n".join(peek.summarize(path))

    assert "조건 시트를 찾지 못했습니다" in text
    assert "[Cover] 1행" in text and "[RTE_Result] 1행" in text and "[<글 11자>] 0행" in text
    assert "secret" not in text and "보고서" not in text


def _secret_workbook(tmp_path: Path) -> Path:
    folder = tmp_path / "secret_dir"
    folder.mkdir()
    book = Workbook()
    book.active.title = "secret_Cover"
    book.active.append(["secret 표지"])
    book.create_sheet("MISRA_Rule_Result").append(["secret_func", r"C:\Users\secret\a.c"])
    sheet = book.create_sheet("MISRA_secret_Rule")
    sheet.append(["Rule", "Category", "secret 열", "Owner", "Description"])
    for i in range(50):
        owner = "secret_kim" if i % 2 else "secret_lee"
        sheet.append([f"{i}.1", CATEGORIES[i % 3], 1234567, owner, f"secret_func 설명 {i}"])
    path = folder / "secret_report.xlsx"
    book.save(path)
    return path


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], path: Path) -> str:
    monkeypatch.setattr(peek, "clear_screen", lambda: None)
    monkeypatch.setattr(sys, "argv", ["peek_conditions.py", f'"{path}"'])
    peek.main()
    return capsys.readouterr().out


def test_no_company_names_reach_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run(monkeypatch, capsys, _secret_workbook(tmp_path))

    assert "조건 시트 1개 / 그 밖의 시트 2개" in out
    assert "secret" not in out.casefold()
    assert "1234567" not in out and "<숫자 7자리>" in out  # 사번 같은 긴 번호
    assert "Category: Mandatory 17, Required 17, Advisory 16" in out
    assert "<글 10자> 25" in out  # Owner 값 종류는 길이만
    assert str(tmp_path) not in out and "Temp" not in out


def test_open_error_shows_only_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run(monkeypatch, capsys, tmp_path / "secret_dir" / "secret.xlsx")

    assert "FileNotFoundError" in out and "경로를 확인하세요" in out
    assert "secret" not in out


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("10.4", "10.4"),
        ("<= 10", "<= 10"),
        ("Rule 10.4", "Rule 10.4"),
        ("Category: Advisory", "Category: Advisory"),
        ("MISRA C:2012", "MISRA C:2012"),
        ("Number of Called Functions", "Number of Called Functions"),
        ("Out of bounds array index", "Out of bounds array index"),
        ("Red 0건, Orange 검토", "Red 0건, Orange 검토"),
        ("R2023b", "R2023b"),
        ("적용 여부", "적용 여부"),
        ("약어", "약어"),
        ("Data Flow Analysis", "Data Flow Analysis"),
        ("123456", "<숫자 6자리>"),
        ("i2c_init", "<글 8자>"),
        ("A project shall not contain unreachable code", "<글 44자>"),
        ("", ""),
    ],
)
def test_safe_keeps_only_numbers_and_standard_words(value: str, shown: str) -> None:
    assert peek.safe(value) == shown


def _merged_workbook(path: Path) -> Path:
    """병합 셀처럼 빈 열이 끼고, 지표 하나가 여러 행(단계)을 차지하는 조건 시트."""
    book = Workbook()
    book.active.title = "Cover"
    metrics = book.create_sheet("Code Metrics")
    metrics.append([])
    metrics.append([None, "No", "Metric", None, "설명", None, "기준", None, "Pass / Fail"])
    metrics.append(
        [None, 1, "Cyclomatic Complexity", None, "secret 설명", None, "1 ~ 15", None, "Pass"]
    )
    metrics.append([None, None, None, None, None, None, ">= 16", None, "Fail (수정 필요)"])
    rte = book.create_sheet("RTE")
    rte.append(["No", "종류", None, "약어"])
    rte.append([1, "Overflow", None, "OVFL"])
    misra = book.create_sheet("MISRA_C_2012_Rule")
    misra.append(["Guideline", "Mode"])
    for i in range(80):
        misra.append([f"{i}.1", CATEGORIES[i % 3]])
    book.save(path)
    return path


def test_empty_columns_are_dropped_and_blanks_shown_as_dash(tmp_path: Path) -> None:
    screen = peek.summarize(_merged_workbook(tmp_path / "m.xlsx"))
    block = _block(screen, "Code Metrics")

    assert "3행 중 앞 3행 / 헤더 추정 2행 / 열 5개 (빈 열 4개 뺌)" in block[0]
    assert block[1].strip() == "2: No | Metric | 설명 | 기준 | Pass / Fail"
    assert block[2].strip() == "3: 1 | Cyclomatic Complexity | <글 9자> | 1 ~ 15 | Pass"
    assert block[3].strip() == "4: - | - | - | >= 16 | Fail (수정 필요)"
    # 다 들어가는 시트는 값 종류 줄이 없고, 잘리는 MISRA 시트만 있다
    assert len(block) == 1 + 3
    assert any("Mode: Mandatory 27, Required 27, Advisory 26" in line for line in screen)


def test_words_after_the_path_pick_sheets(tmp_path: Path) -> None:
    screen = peek.summarize(_merged_workbook(tmp_path / "m.xlsx"), ("metric", "rte"))
    text = "\n".join(screen)

    assert screen[0].startswith("조건 시트 3개 중 2개 (metric, rte)")
    assert "[Code Metrics]" in text and "[RTE]" in text and "MISRA" not in text
    # 둘 다 화면에 다 들어가므로 값 종류 줄이 없다
    assert not any(line.startswith("  종류 ") for line in screen)


def test_unknown_pick_word_shows_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["peek_conditions.py", "x.xlsx", "secret"])

    assert peek.main() == 2
    out = capsys.readouterr().out
    assert "사용법" in out and "secret" not in out


def test_share_gives_small_needs_first() -> None:
    assert peek.share([3, 100, 50], 30) == [3, 14, 13]
    assert peek.share([2, 2], 30) == [2, 2]
    assert peek.share([5, 5, 5], 0) == [0, 0, 0]
