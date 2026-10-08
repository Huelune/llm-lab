import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fake_llm import MODEL, error, make_client, scripted
from fastapi.testclient import TestClient
from rte_fixtures import CALC_C, finding, fix_answer, rte_excel, rte_row

from llm_lab import web
from llm_lab.fixer import Edit, FixResult
from llm_lab.folder_picker import PickerError
from llm_lab.sources import decode_source
from llm_lab.store import NewRow, Store
from llm_lab.worker import Worker


def run_now(task):
    task()


def make_service(tmp_path, *responses, submit=run_now):
    handler, sent = scripted(*responses)
    store = Store(tmp_path / "fixer.db")
    worker = Worker(store, make_client(handler), MODEL, submit)
    # 브라우저가 실제로 보내는 Host와 같게 둔다
    client = TestClient(web.create_app(store, worker), base_url="http://127.0.0.1:8000")
    return client, store, sent


def source_folder(tmp_path) -> Path:
    """엑셀의 C:\\proj\\src\\calc.c에 해당하는 파일이 든 소스 폴더."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "calc.c").write_bytes(CALC_C)
    return root


def upload(client, excel: bytes, folder):
    files = [("excel", ("rte.xlsx", excel))]
    data = {"source_dir": str(folder)}
    return client.post("/results", files=files, data=data, follow_redirects=False)


EXCEL = rte_excel(
    rte_row("Orange Check"),
    rte_row("Red Check", file=r"C:\proj\src\missing.c"),
    rte_row("Gray Check"),
)


def test_home_shows_upload_steps_and_menu(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert "<title>새 분석 · Polyspace RTE 수정 제안</title>" in page
    assert 'href="/results">작업 목록</a>' in page
    assert 'name="excel"' in page
    assert 'name="source_dir"' in page
    assert 'action="/results"' in page
    assert "아직 작업이 없습니다" in page


def test_rejects_other_host_names(tmp_path):
    # DNS 리바인딩: 다른 사이트가 자기 도메인으로 이 서버(회사 소스·diff)를 읽지 못해야 한다
    client, _, _ = make_service(tmp_path)

    assert client.get("/api/results/1", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/", headers={"host": "localhost:8000"}).status_code == 200


def test_upload_processes_rows_and_shows_result_page(tmp_path):
    client, _, sent = make_service(tmp_path, fix_answer())

    folder = source_folder(tmp_path)

    response = upload(client, EXCEL, folder)

    assert response.status_code == 303
    assert response.headers["location"] == "/results/1"
    page = client.get("/results/1").text
    assert "<title>작업 1 결과 · rte.xlsx · Polyspace RTE 수정 제안</title>" in page
    assert '<span class="badge green">완료</span>' in page
    for value, label in [(1, "Red"), (1, "Orange"), (1, "제외 (Gray 등)"), (1, "수정")]:
        assert f'<div class="v">{value}</div><div class="k">{label}</div>' in page
    assert '<span class="add">+    return (b != 0) ? a / b : 0;</span>' in page
    assert "폴더에 missing.c이(가) 없음" in page
    assert 'type="checkbox" name="row" value="1"' in page
    assert 'data-kind="fix"' in page and 'data-kind="problem"' in page
    assert str(folder) in page  # git apply를 실행할 폴더 = 지정한 소스 폴더
    assert 'data-copy="git -c core.autocrlf=false apply --check fixes.patch"' in page
    assert 'action="/results/1/fixes.patch"' in page
    assert 'id="toggle-all"' in page  # 수정 모두 선택
    assert 'http-equiv="refresh"' not in page
    assert len(sent) == 1  # 소스가 없는 행은 LLM에 보내지 않는다


def test_result_json(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    folder = source_folder(tmp_path)
    upload(client, EXCEL, folder)

    data = client.get("/api/results/1").json()

    assert data["state"] == "done"
    assert data["base_folder"] == str(folder)
    assert [(r["state"], r["decision"]) for r in data["rows"]] == [
        ("done", "fix"),
        ("no_source", None),
    ]


def test_results_list_page(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results").text

    assert "<title>작업 목록 · Polyspace RTE 수정 제안</title>" in page
    assert 'href="/results/1">결과 보기' in page
    assert "rte.xlsx" in page
    assert '<span class="badge red">1</span> <span class="badge orange">1</span>' in page
    assert '<span class="badge green">완료</span>' in page


def test_bad_excel_is_rejected_with_reason(tmp_path):
    client, _, _ = make_service(tmp_path)

    response = upload(client, b"not excel", source_folder(tmp_path))

    assert response.status_code == 400
    assert "엑셀(.xlsx) 파일로 읽을 수 없습니다" in response.text
    assert 'href="/">새 분석' in response.text


def test_download_patch_for_chosen_rows(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))

    response = client.post("/results/1/fixes.patch", data={"row": ["1"]})

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="fixes.patch"'
    # 패치 경로는 소스 폴더 기준
    assert response.content.startswith(b"--- a/src/calc.c\n+++ b/src/calc.c\n")
    assert b"+    return (b != 0) ? a / b : 0;\n" in response.content


def test_patch_needs_a_chosen_fix(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))

    assert client.post("/results/1/fixes.patch", data={}).status_code == 400
    # 소스 없는 행
    assert client.post("/results/1/fixes.patch", data={"row": ["2"]}).status_code == 400


def test_overlapping_fixes_conflict(tmp_path):
    other = {"original": "    return a / b;", "replacement": "    return b ? a / b : -1;"}
    client, _, _ = make_service(tmp_path, fix_answer(), fix_answer(edits=[other]))
    excel = rte_excel(rte_row("Orange Check"), rte_row("Red Check", check="Overflow"))
    upload(client, excel, source_folder(tmp_path))

    response = client.post("/results/1/fixes.patch", data={"row": ["1", "2"]})

    assert response.status_code == 409
    assert "행 2와 행 3" in response.text


def test_running_job_page_refreshes_and_shows_progress(tmp_path):
    queued = []
    client, _, _ = make_service(tmp_path, submit=queued.append)
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results/1").text

    assert 'http-equiv="refresh"' in page
    assert '<span class="badge blue">처리 중 1/2</span>' in page
    assert '<div class="progress"><div style="width:50%"></div></div>' in page
    assert 'type="checkbox"' not in page
    assert len(queued) == 1


def test_stopped_job_can_be_retried(tmp_path):
    client, store, _ = make_service(tmp_path, error(401, "invalid key"), fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))
    page = client.get("/results/1").text
    assert '<span class="badge red">멈춤</span>' in page
    assert "[인증]" in page
    assert 'action="/results/1/retry"' in page

    response = client.post("/results/1/retry", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/results/1"
    assert store.job(1).state == "done"


def test_unknown_job_is_404(tmp_path):
    client, _, _ = make_service(tmp_path)

    assert client.get("/results/9").status_code == 404
    assert client.get("/api/results/9").status_code == 404


def test_excel_without_red_or_orange_rows(tmp_path):
    client, _, sent = make_service(tmp_path)

    upload(client, rte_excel(rte_row("Gray Check")), source_folder(tmp_path))

    assert "고칠 행 없음" in client.get("/results/1").text
    assert sent == []


def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)

    assert web.main([]) == 1
    assert "설정 오류" in capsys.readouterr().err


def start_main(tmp_path, monkeypatch, argv):
    """실제 서버 대신 앱을 받아 두기만 하고 main을 돌린다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", MODEL)
    started, workers, opened = [], [], []
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **kwargs: started.append(kwargs))
    monkeypatch.setattr(
        web, "Worker", lambda *args, **kwargs: workers.append(kwargs) or Worker(*args, **kwargs)
    )
    monkeypatch.setattr(web, "open_when_ready", lambda url, port: opened.append((url, port)))
    assert web.main(argv) == 0
    return started, workers, opened


def test_main_keeps_conversation_logs_in_private(tmp_path, monkeypatch):
    started, workers, _ = start_main(tmp_path, monkeypatch, ["--no-browser"])

    assert started == [{"host": "127.0.0.1", "port": 8000}]
    assert workers[0]["log_dir"] == Path("private/llm-logs")


def test_main_opens_browser_unless_told_not_to(tmp_path, monkeypatch):
    _, _, opened = start_main(tmp_path, monkeypatch, ["--port", "8123"])
    for _ in range(50):  # 브라우저 열기는 별도 스레드에서 돈다
        if opened:
            break
        time.sleep(0.02)
    assert opened == [("http://127.0.0.1:8123/", 8123)]

    _, _, opened = start_main(tmp_path, monkeypatch, ["--no-browser"])
    time.sleep(0.1)
    assert opened == []


def test_open_when_ready_waits_for_the_port(monkeypatch):
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        port = listening.getsockname()[1]

        web.open_when_ready(f"http://127.0.0.1:{port}/", port)

    assert opened == [f"http://127.0.0.1:{port}/"]


def test_open_when_ready_gives_up_quietly(monkeypatch):
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # 아무도 듣지 않는 포트

    web.open_when_ready(f"http://127.0.0.1:{port}/", port, timeout=0.5)

    assert opened == []


def test_main_refuses_database_from_newer_version(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", MODEL)
    Store(web.DB_PATH)
    with closing(sqlite3.connect(web.DB_PATH)) as db:
        db.execute("PRAGMA user_version = 999")

    assert web.main(["--no-browser"]) == 1
    assert "새 버전" in capsys.readouterr().err


def test_missing_or_empty_folder_is_rejected(tmp_path):
    client, _, sent = make_service(tmp_path)

    for value in [tmp_path / "nope", "   "]:
        response = upload(client, EXCEL, value)
        assert response.status_code == 400
        assert "소스 폴더" in response.text
    assert sent == []


def test_folder_path_copied_with_quotes_is_accepted(tmp_path):
    # 탐색기의 '경로로 복사'는 경로를 따옴표로 감싼다
    client, store, _ = make_service(tmp_path, fix_answer())
    folder = source_folder(tmp_path)

    assert upload(client, EXCEL, f'"{folder}"').status_code == 303
    assert store.job(1).source_root == str(folder)


def test_home_suggests_recent_folders(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    folder = source_folder(tmp_path)
    upload(client, EXCEL, folder)

    assert f'<option value="{folder}">' in client.get("/").text


def test_jobs_from_uploaded_files_still_download_patches(tmp_path):
    # 폴더 지정 전(파일을 하나씩 올리던 때) 만든 작업은 엑셀 경로로 기준 폴더를 정한다
    client, store, _ = make_service(tmp_path)
    source = decode_source("calc.c", CALC_C)
    job_id = store.create_job(
        "rte.xlsx", "RTE_Result", 0, [source], [NewRow(finding(), "calc.c", "pending")]
    )
    row = store.rows(job_id)[0]
    start = source.text.index("return a / b;")
    edit = Edit(start, start + len("return a / b;"), "return b ? a / b : 0;")
    store.set_row(row.id, "done", result=FixResult("fix", "이유", (edit,), "diff"))
    store.finish_if_done(job_id)

    response = client.post(f"/results/{job_id}/fixes.patch", data={"row": [str(row.id)]})

    assert response.status_code == 200
    assert response.content.startswith(b"--- a/calc.c\n")
    assert "C:\\proj\\src" in client.get(f"/results/{job_id}").text


def test_folder_picker_needs_the_page_header(tmp_path, monkeypatch):
    # 다른 사이트가 몰래 보낸 요청으로는 폴더 선택 창이 뜨지 않아야 한다
    client, _, _ = make_service(tmp_path)
    opened = []
    monkeypatch.setattr(web, "pick_folder", lambda initial: opened.append(initial))

    assert client.post("/pick-folder", data={"initial": ""}).status_code == 403
    assert opened == []


def test_folder_picker_returns_path_cancel_or_error(tmp_path, monkeypatch):
    client, _, _ = make_service(tmp_path)
    headers = {"X-Folder-Picker": "1"}
    answers = iter([r"C:\work\src", None, PickerError("폴더 선택 창(tkinter)이 없습니다")])

    def fake_pick(initial):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(web, "pick_folder", fake_pick)

    def pick():
        return client.post("/pick-folder", data={"initial": r"C:\work"}, headers=headers).json()

    assert pick() == {"path": r"C:\work\src"}
    assert pick() == {"path": None}
    assert pick() == {"error": "폴더 선택 창(tkinter)이 없습니다"}


def test_home_has_folder_picker_button(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'id="pick-folder"' in page
    assert "폴더 찾기" in page
