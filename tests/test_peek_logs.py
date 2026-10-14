"""scripts/peek_logs.py: 대화 기록에서 직접 수정 행을 개수로만 센다 (회사 정보는 안 찍음)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from rte_fixtures import finding

from llm_lab.fixer import Exchange
from llm_lab.transcript import render_log, write_log

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peek_logs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peek_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


peek = _load()

HEAD = "Polyspace Code Prover results in one C function:\n\nFinding 1:\n- File: secret_file.c\n"
CODE = "Code (lines 1-4 of secret_file.c):\n```c\n    1| int secret_func(void)\n```"
EDITABLE = (
    "\n\nDefinitions from other places. Those marked editable may be changed.\n\n"
    "```c\n// editable: gen/Rte_secret.c lines 1-4\n    1| int secret_api(void)\n```"
)
CLIPPED = (
    "\n\n```c\n// editable: gen/Rte_secret_big.c lines 1-200\n    1| int secret_big(void)"
    "\n... (120 more lines, not editable)\n```"
)
CALLER = (
    "\n\nCallers of secret_func(), for the values that reach its parameters.\n\n"
    "```c\n// editable caller: app/secret_main.c lines 1-5 (in secret_caller())\n"
    "    1| void secret_caller(void)\n```"
)
REFERENCE = (
    "\n\nDefinitions from other places, for reference only.\n\n"
    "```c\n// inc/secret.h line 3\n#define SECRET_LIMIT 4\n```"
)


def answer(decision: str, edits: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "findings": [{"number": 1, "decision": decision, "reason": "secret reason text"}],
            "edits": edits or [],
        }
    )


def log(folder: Path, job: int, row: int, user: str, content: str) -> None:
    exchange = Exchange(
        [{"role": "system", "content": "secret system"}, {"role": "user", "content": user}],
        raw={"secret": "raw"},
        content=content,
        finish_reason="stop",
    )
    text = render_log(
        job_id=job,
        kind="RTE",
        findings=[finding(row=row, file=r"C:\secret_dir\secret_file.c", function="secret_func")],
        source_name="secret_file.c",
        model="secret-model",
        endpoint="https://secret.example/v1/chat/completions",
        extra_body={},
        exchanges=[exchange],
        outcomes=["secret outcome"],
    )
    write_log(folder, job, row, text)


def sample(folder: Path) -> None:
    log(folder, 3, 2, HEAD + CODE + EDITABLE, answer("manual"))
    log(folder, 3, 3, HEAD + CODE + EDITABLE + CLIPPED, answer("manual"))
    log(folder, 3, 4, HEAD + CODE + CALLER, answer("manual"))
    log(folder, 3, 5, HEAD + CODE + REFERENCE, answer("manual"))
    log(folder, 3, 6, HEAD + CODE, answer("manual"))
    other = {"file": "gen/Rte_secret.c", "original": "a", "replacement": "b"}
    log(folder, 3, 7, HEAD + CODE + EDITABLE, answer("fix", [other]))
    log(folder, 3, 8, HEAD + CODE, answer("no_fix"))
    log(folder, 2, 2, HEAD + CODE, answer("manual"))  # 예전 작업은 세지 않는다


def test_counts_manual_rows_by_what_was_sent(tmp_path):
    sample(tmp_path)

    screen = "\n".join(peek.summarize(tmp_path, pattern="rte"))

    assert "작업 3: 묶음 7개" in screen
    assert "수정 1 / 수정 불필요 1 / 직접 수정 5" in screen
    assert "고칠 수 있는 정의가 붙음" in screen
    counts = dict(peek.manual_counts(peek.read_logs(tmp_path, 3), pattern="rte"))
    assert counts == {
        "editable": 2,
        "editable_pattern": 2,
        "caller": 1,
        "reference_only": 1,
        "none": 1,
        "clipped": 1,
        "capped": 0,
    }
    assert peek.other_file_fixes(peek.read_logs(tmp_path, 3), pattern="rte") == (1, 1)


def test_newest_job_is_the_default(tmp_path):
    sample(tmp_path)

    assert peek.newest_job(tmp_path) == 3


def test_screen_never_shows_names_paths_or_text(tmp_path):
    sample(tmp_path)

    screen = "\n".join(peek.summarize(tmp_path, pattern="rte"))

    assert "secret" not in screen.casefold()
    assert "Rte_" not in screen
    assert len(screen.splitlines()) <= 40
    assert max(len(line) for line in screen.splitlines()) <= 118


def test_missing_folder_or_job_says_what_to_do(tmp_path):
    assert "대화 기록이 없습니다" in "\n".join(peek.summarize(tmp_path / "nope"))
    sample(tmp_path)
    assert "작업 9의 대화 기록이 없습니다" in "\n".join(peek.summarize(tmp_path, job=9))
