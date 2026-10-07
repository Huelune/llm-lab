from fake_llm import MODEL, error, make_client, scripted
from fastapi.testclient import TestClient
from rte_fixtures import CALC_C, fix_answer, rte_excel, rte_row

from llm_lab import web
from llm_lab.store import Store
from llm_lab.worker import Worker


def run_now(task):
    task()


def make_service(tmp_path, *responses, submit=run_now):
    handler, sent = scripted(*responses)
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, submit)
    return TestClient(web.create_app(store, worker)), store, sent


def upload(client, excel: bytes, *sources: tuple[str, bytes]):
    files = [("excel", ("rte.xlsx", excel))]
    files += [("sources", (name, data)) for name, data in sources]
    return client.post("/jobs", files=files, follow_redirects=False)


EXCEL = rte_excel(
    rte_row("Orange Check"),
    rte_row("Red Check", file=r"C:\proj\src\missing.c"),
    rte_row("Gray Check"),
)


def test_index_shows_upload_form(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="excel"' in page
    assert 'name="sources" multiple' in page


def test_upload_processes_rows_and_shows_results(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer())

    response = upload(client, EXCEL, ("calc.c", CALC_C))

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/1"
    page = client.get("/jobs/1").text
    assert "상태: 완료" in page
    assert "Red 1 · Orange 1 · 제외(Gray 등) 1" in page
    assert "수정 1 · 수정 불필요 0" in page
    assert '<span class="add">+    return (b != 0) ? a / b : 0;</span>' in page
    assert "업로드한 소스에 missing.c이(가) 없음" in page
    assert 'type="checkbox" name="row" value="1"' in page
    assert "C:\\proj\\src" in page  # git apply를 실행할 폴더
    assert "git -c core.autocrlf=false apply --check fixes.patch" in page
    assert 'http-equiv="refresh"' not in page
    assert len(sent) == 1  # 소스가 없는 행은 LLM에 보내지 않는다
    data = client.get("/api/jobs/1").json()
    assert data["state"] == "done"
    assert data["base_folder"] == "C:/proj/src"
    assert [(r["state"], r["decision"]) for r in data["rows"]] == [
        ("done", "fix"),
        ("no_source", None),
    ]


def test_bad_excel_is_rejected_with_reason(tmp_path):
    client, _, _ = make_service(tmp_path)

    response = upload(client, b"not excel", ("calc.c", CALC_C))

    assert response.status_code == 400
    assert "엑셀(.xlsx) 파일로 읽을 수 없습니다" in response.text


def test_download_patch_for_chosen_rows(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, ("calc.c", CALC_C))

    response = client.post("/jobs/1/patch", data={"row": ["1"]})

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="fixes.patch"'
    assert response.content.startswith(b"--- a/calc.c\n+++ b/calc.c\n")
    assert b"+    return (b != 0) ? a / b : 0;\n" in response.content


def test_patch_needs_a_chosen_fix(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, ("calc.c", CALC_C))

    assert client.post("/jobs/1/patch", data={}).status_code == 400
    assert client.post("/jobs/1/patch", data={"row": ["2"]}).status_code == 400  # 소스 없는 행


def test_overlapping_fixes_conflict(tmp_path):
    other = {"original": "    return a / b;", "replacement": "    return b ? a / b : -1;"}
    client, _, _ = make_service(tmp_path, fix_answer(), fix_answer(edits=[other]))
    excel = rte_excel(rte_row("Orange Check"), rte_row("Red Check", check="Overflow"))
    upload(client, excel, ("calc.c", CALC_C))

    response = client.post("/jobs/1/patch", data={"row": ["1", "2"]})

    assert response.status_code == 409
    assert "행 2와 행 3" in response.text


def test_running_job_page_refreshes_without_checkboxes(tmp_path):
    queued = []
    client, _, _ = make_service(tmp_path, submit=queued.append)
    upload(client, EXCEL, ("calc.c", CALC_C))

    page = client.get("/jobs/1").text

    assert 'http-equiv="refresh"' in page
    assert "처리 중 (5초마다 새로 고침)" in page
    assert 'type="checkbox"' not in page
    assert len(queued) == 1


def test_stopped_job_can_be_retried(tmp_path):
    client, store, _ = make_service(tmp_path, error(401, "invalid key"), fix_answer())
    upload(client, EXCEL, ("calc.c", CALC_C))
    page = client.get("/jobs/1").text
    assert "멈춤: [인증]" in page
    assert "오류 행 다시 시도" in page

    response = client.post("/jobs/1/retry", follow_redirects=False)

    assert response.status_code == 303
    assert store.job(1).state == "done"


def test_unknown_job_is_404(tmp_path):
    client, _, _ = make_service(tmp_path)

    assert client.get("/jobs/9").status_code == 404
    assert client.get("/api/jobs/9").status_code == 404


def test_excel_without_red_or_orange_rows(tmp_path):
    client, _, sent = make_service(tmp_path)

    upload(client, rte_excel(rte_row("Gray Check")), ("calc.c", CALC_C))

    assert "고칠 행 없음" in client.get("/jobs/1").text
    assert sent == []


def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)

    assert web.main([]) == 1
    assert "설정 오류" in capsys.readouterr().err
