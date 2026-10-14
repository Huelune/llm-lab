"""scripts/peek_misra.py: MISRA 결과 행을 규칙 장별로, 빠진 이유와 분류로 센다 (개수만)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_row
from rte_fixtures import workbook_bytes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peek_misra.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peek_misra", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


peek = _load()


def excel(tmp_path: Path) -> Path:
    rows = [
        misra_row("10.4 Operands secret_x", category="Required"),
        misra_row("10.4 Operands secret_y", category="Required"),
        misra_row("2.2 dead code secret", category="Advisory", group="2 Unused code"),
        misra_row("1.3 undefined secret", category="Required", group="1 The implementation"),
        misra_row("12.1 precedence secret", category="Required"),  # 조건 시트는 Advisory
        misra_row("secret no number", category="Required"),
    ]
    path = tmp_path / "secret_result.xlsx"
    path.write_bytes(
        workbook_bytes({"MISRA_rules": CONDITION_ROWS, "MISRA_Result": [MISRA_HEADER, *rows]})
    )
    return path


def test_counts_rows_by_rule_chapter_and_fate(tmp_path):
    counts = peek.chapter_counts(excel(tmp_path))

    assert counts["10"] == {"rows": 2, "disabled": 0, "Required": 2}
    assert counts["2"] == {"rows": 1, "disabled": 1}
    assert counts["1"] == {"rows": 1, "disabled": 0, "Required": 1}
    assert counts["12"] == {"rows": 1, "disabled": 0, "Advisory": 1}
    assert counts["번호 없음"]["rows"] == 1


def test_screen_is_numbers_and_rule_chapters_only(tmp_path):
    screen = "\n".join(peek.summarize(excel(tmp_path)))

    assert "규칙 2장: 행 1, 조건 시트에서 꺼짐 1" in screen
    assert "규칙 10장: 행 2, 조건 시트에서 꺼짐 0, Required 2" in screen
    assert "secret" not in screen.casefold()
    assert len(screen.splitlines()) <= 40


def test_missing_file_or_sheet_says_what_to_do(tmp_path):
    assert "엑셀 파일을 찾지 못했습니다" in "\n".join(peek.summarize(tmp_path / "none.xlsx"))
    other = tmp_path / "secret_other.xlsx"
    other.write_bytes(workbook_bytes({"Summary": [["x"]]}))
    assert "MISRA 결과 시트를 찾지 못했습니다" in "\n".join(peek.summarize(other))
