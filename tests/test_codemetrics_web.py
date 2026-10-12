"""CodeMetrics 작업: 엑셀 올리기 → 함수마다 LLM 한 번 → 결과 화면 → 패치 적용."""

import shutil
import subprocess

import pytest
from codemetrics_fixtures import (
    GRADE_C,
    GRADE_REFACTORED,
    code_answer,
    metric_row,
    metrics_excel,
)
from fake_llm import MODEL, make_client, scripted
from fastapi.testclient import TestClient

from llm_lab import web
from llm_lab.patch import APPLY_COMMAND
from llm_lab.store import Store
from llm_lab.worker import Worker

SCALE_PATH = r"C:\proj\src\scale.c"
SCALE_C = (
    b"int scale(int x)\n{\n    if (x > 10) {\n        x = 10;\n    }\n    return x;\n}\n".replace(
        b"\n", b"\r\n"
    )
)
SCALE_REFACTORED = """\
/* 상한을 적용한다. */
static int scale_limit(int x)
{
    int limited = x;
    if (limited > 10) {
        limited = 10;
    }
    return limited;
}

int scale(int x)
{
    x = scale_limit(x);
    return x;
}
"""
CHOICES = ["codemetrics:Fail", "codemetrics:가운데", "codemetrics:단계 모름"]
EXCEL = metrics_excel(
    metric_row(actual=24),  # 엑셀 2행: grade, Fail
    metric_row("Number of Calling Functions", threshold=4, actual=6),  # 3행: grade, 가운데
    metric_row(actual=15, function="scale()", line=1, file=SCALE_PATH),  # 4행: scale, 가운데
)
ANSWERS = (
    code_answer(
        ("fix", "등급 계산을 도우미로 옮김"), ("no_fix", "공용 함수"), code=GRADE_REFACTORED
    ),
    code_answer(("fix", "상한 처리를 도우미로 옮김"), code=SCALE_REFACTORED),
)


def run_now(task):
    task()


def make_service(tmp_path, *responses):
    handler, sent = scripted(*responses)
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, run_now)
    client = TestClient(web.create_app(store, worker), base_url="http://127.0.0.1:8000")
    return client, store, sent


def source_folder(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "grade.c").write_bytes(GRADE_C)  # CP949, LF
    (root / "src" / "scale.c").write_bytes(SCALE_C)  # UTF-8, CRLF
    return root


def upload(client, excel, folder, choices=CHOICES):
    files = [("excel", ("metrics.xlsx", excel))]
    data = {"source_dir": str(folder), "kind": "codemetrics", "choice": list(choices)}
    return client.post("/results", files=files, data=data, follow_redirects=False)


def test_home_offers_codemetrics_with_every_stage_checked(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="kind" value="codemetrics">' in page
    for stage in ("Fail", "가운데", "단계 모름"):
        assert f'name="choice" value="codemetrics:{stage}" checked' in page


def test_one_request_per_function_and_middle_fixes_are_left_to_people(tmp_path):
    client, store, sent = make_service(tmp_path, *ANSWERS)

    assert upload(client, EXCEL, source_folder(tmp_path)).status_code == 303

    job = store.job(1)
    assert (job.kind, job.sheet, job.state) == ("codemetrics", "CodeMetrics_Result", "done")
    assert len(sent) == 2  # 함수마다 한 번, Fail이 든 grade부터
    first = sent[0]["messages"][1]["content"]
    assert "Replace lines 3-15 of src/grade.c" in first
    assert "Finding 2:\n- check: Number of Calling Functions" in first
    assert sent[0]["response_format"]["json_schema"]["schema"]["required"] == ["findings", "code"]
    decisions = [(row.finding.row, row.result.decision) for row in store.rows(1) if row.result]
    assert decisions == [(2, "fix"), (3, "no_fix"), (4, "fix")]

    page = client.get("/results/1").text
    assert "값 24 · 기준 10 · 조건 시트: Fail (고칠 것)" in page
    assert "조건 시트: 지표 3개" in page
    assert 'name="row" value="1" data-auto="1"' in page  # Fail
    assert 'name="row" value="3" data-auto="0"' in page  # 가운데
    assert "자동으로 고르지 않은 수정 1개는 직접 고르세요" in page


def test_unchosen_stages_are_not_sent(tmp_path):
    only_fail = code_answer(("fix", "등급 계산을 도우미로 옮김"), code=GRADE_REFACTORED)
    client, store, sent = make_service(tmp_path, only_fail)

    upload(client, EXCEL, source_folder(tmp_path), choices=["codemetrics:Fail"])

    assert len(sent) == 1
    assert [row.finding.row for row in store.rows(1)] == [2]
    assert "가운데 (고르지 않음) 2" in store.job(1).note


@pytest.mark.skipif(shutil.which("git") is None, reason="git 없음")
def test_patch_applies_byte_for_byte_to_cp949_and_crlf_files(tmp_path):
    client, _, _ = make_service(tmp_path, *ANSWERS)
    folder = source_folder(tmp_path)
    upload(client, EXCEL, folder)

    response = client.post("/results/1/fixes.patch", data={"row": ["1", "3"]})
    assert response.status_code == 200
    (folder / "fixes.patch").write_bytes(response.content)
    for args in (["--check"], []):
        subprocess.run(
            [*APPLY_COMMAND.split(), *args, "fixes.patch"],
            cwd=folder,
            check=True,
            capture_output=True,
        )

    grade = ('#include "grade.h"\n\n' + GRADE_REFACTORED).encode("cp949")
    assert (folder / "src" / "grade.c").read_bytes() == grade
    assert (folder / "src" / "scale.c").read_bytes() == SCALE_REFACTORED.replace(
        "\n", "\r\n"
    ).encode()
