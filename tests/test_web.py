import json
import socket
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from fake_llm import MODEL, error, make_client, scripted
from fastapi.testclient import TestClient
from misra_fixtures import CONDITION_ROWS, MISRA_HEADER, misra_row
from rte_fixtures import (
    CALC_C,
    DIVIDE_FIX,
    REASON,
    RTE_HEADER,
    answer,
    finding,
    fix_answer,
    rte_excel,
    rte_row,
    workbook_bytes,
)

from llm_lab import web
from llm_lab.config import OPTIONAL_VARS, REQUIRED_VARS
from llm_lab.fixer import Edit, FixResult
from llm_lab.folder_picker import PickerError
from llm_lab.kinds import KINDS, RTE
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


def upload(
    client,
    excel: bytes,
    folder,
    kind="rte",
    choices=("rte:Red", "rte:Orange"),
    mode=None,
):
    files = [("excel", ("rte.xlsx", excel))]
    data = {"source_dir": str(folder), "kind": kind, "choice": list(choices)}
    if mode:
        data["mode"] = [mode]
    return client.post("/results", files=files, data=data, follow_redirects=False)


EXCEL = rte_excel(
    rte_row("Orange Check"),
    rte_row("Red Check", file=r"C:\proj\src\missing.c"),
    rte_row("Gray Check"),
)


def test_home_shows_upload_steps_and_menu(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert "<title>새 분석 · Polyspace 수정 제안</title>" in page
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
    assert "<title>작업 1 결과 · rte.xlsx · Polyspace 수정 제안</title>" in page
    assert "RTE · 시트 RTE_Result" in page
    assert "제외: Gray Check 1" in page  # 작업 메모
    assert '<span class="badge green">완료</span>' in page
    for color, label in [("red", "Red"), ("orange", "Orange"), ("blue", "수정")]:
        assert f'<span class="badge {color}">{label}</span> <b>1</b>' in page
    assert '<span class="badge gray">제외 1</span>' in page
    assert '<span class="add">+    return (b != 0) ? a / b : 0;</span>' in page
    assert "폴더에 missing.c이(가) 없음" in page
    assert 'type="checkbox" name="row" value="1"' in page
    assert 'data-kind="fix"' in page and 'data-kind="problem"' in page
    assert str(folder) in page  # git apply를 실행할 폴더 = 지정한 소스 폴더
    assert 'data-copy="git -c core.autocrlf=false apply --check fixes.patch"' in page
    assert 'action="/results/1/fixes.patch"' in page
    assert 'id="toggle-all"' in page  # 수정 모두 선택
    assert "data-refresh=" not in page
    assert len(sent) == 1  # 소스가 없는 행은 LLM에 보내지 않는다


def test_top_counts_keep_every_target_even_at_zero(tmp_path):
    # 패치 뒤 다시 분석하면 Red가 0개일 때가 많다. 0개여도 개수 줄에서 빠지지 않는다
    client, _, _ = make_service(tmp_path, fix_answer())

    upload(client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path))

    page = client.get("/results/1").text
    assert '<span class="badge red">Red</span> <b>0</b>' in page
    assert '<span class="badge orange">Orange</span> <b>1</b>' in page


def test_home_form_shows_a_spinner_while_the_excel_is_read(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    # 분석 시작을 누르면 단추가 잠기고 스피너와 안내가 보인다 (엑셀 읽기가 몇 초 걸린다)
    assert '<form id="analyze"' in page
    assert "엑셀을 읽는 중" in page and ".spinner {" in page


def test_pages_carry_an_inline_favicon_and_favicon_ico_is_served(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text
    icon = client.get("/favicon.ico")

    # 외부 자원을 읽지 않도록 아이콘은 data URI로 head에 들어간다
    assert '<link rel="icon" href="data:image/svg+xml,' in page
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in icon.content


def test_result_json(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    folder = source_folder(tmp_path)
    upload(client, EXCEL, folder)

    data = client.get("/api/results/1").json()

    assert (data["state"], data["kind"], data["note"]) == (
        "done",
        "rte",
        "가드 허용 · 제외: Gray Check 1 · 참고 정의 1개",
    )
    # 소스를 찾은 행은 파일:함수 묶음에 들어가고, 소스가 없는 행은 묶음이 없다
    assert [row["group"] for row in data["rows"]] == ["src/calc.c:divide", ""]
    assert data["base_folder"] == str(folder)
    assert [(r["state"], r["decision"]) for r in data["rows"]] == [
        ("done", "fix"),
        ("no_source", None),
    ]


def test_results_list_page(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results").text

    assert "<title>작업 목록 · Polyspace 수정 제안</title>" in page
    assert 'href="/results/1">결과 보기' in page
    assert "rte.xlsx" in page
    assert (
        'RTE <span class="badge red">Red 1</span> <span class="badge orange">Orange 1</span>'
        in (page)
    )
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
    # 다른 함수의 행이라 따로 보내는데, 답이 같은 줄을 고친다
    excel = rte_excel(
        rte_row("Orange Check"), rte_row("Red Check", check="Overflow", function="other")
    )
    upload(client, excel, source_folder(tmp_path))

    response = client.post("/results/1/fixes.patch", data={"row": ["1", "2"]})

    assert response.status_code == 409
    assert "행 2와 행 3" in response.text


def test_running_job_page_refreshes_and_shows_progress(tmp_path):
    queued = []
    client, _, _ = make_service(tmp_path, submit=queued.append)
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results/1").text

    # 새로 고침은 스크립트가 하고(location.reload), 보고 있던 탭은 주소의 #으로 남긴다
    assert '<main data-refresh="5">' in page
    assert 'http-equiv="refresh"' not in page
    assert '<span class="spinner"></span>1/2 처리 · 5초마다 새로 고칩니다' in page
    assert "location.reload()" in page
    assert "location.hash" in page and "replaceState" in page
    # 다시 읽기 전에 스크롤 위치를 저장했다가 되돌리고, "보기"로 간 카드는 강조한다
    assert "sessionStorage" in page and "scrollRestoration" in page
    assert ".item.hit" in page and "classList.add('hit')" in page
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
    assert ">이어서 처리</button>" in page

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

    assert "분석할 행이 없습니다" in client.get("/results/1").text
    assert sent == []


def test_home_offers_each_kind_with_its_targets(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="kind" value="rte" checked' in page
    assert 'name="kind" value="misra">' in page
    assert 'name="choice" value="rte:Red" checked' in page
    assert 'name="choice" value="misra:Mandatory" checked' in page
    assert 'name="choice" value="misra:Advisory">' in page  # Advisory는 처음에 꺼져 있다
    assert '<div class="choices" data-choices="misra" hidden>' in page


def test_unknown_kind_or_no_target_is_rejected(tmp_path):
    client, _, sent = make_service(tmp_path)
    folder = source_folder(tmp_path)

    assert upload(client, EXCEL, folder, kind="cobol").status_code == 400
    response = upload(client, EXCEL, folder, choices=["misra:Required"])  # RTE 대상이 없다

    assert response.status_code == 400
    assert "분석할 대상을 하나 이상 고르세요." in response.text
    assert sent == []


MISRA_EXCEL = workbook_bytes(
    {
        "RTE_Result": [RTE_HEADER, rte_row()],
        "MISRA_Result": [
            MISRA_HEADER,
            misra_row("10.4 Operands", category="Advisory"),  # 조건 시트로 Required
            misra_row("21.3 Memory", category="Mandatory"),  # 같은 줄 → 함께 보낸다
            misra_row("2.2 Dead code"),  # 조건 시트에서 꺼짐
            misra_row("12.1 Precedence", category="Required"),  # 조건 시트로 Advisory → 안 고름
        ],
        "Rules": CONDITION_ROWS,
    }
)


def test_misra_job_sends_one_line_together_and_shows_categories(tmp_path):
    reply = answer(("fix", REASON), ("fix", "같은 수정으로 함께 고침"), edits=[DIVIDE_FIX])
    client, store, sent = make_service(tmp_path, reply)
    choices = ["misra:Mandatory", "misra:Required", "rte:Red"]

    response = upload(client, MISRA_EXCEL, source_folder(tmp_path), "misra", choices)

    assert response.status_code == 303
    assert len(sent) == 1  # 같은 줄의 두 행을 한 번에 보냈다
    job = store.job(1)
    assert (job.kind, job.sheet) == ("misra", "MISRA_Result")
    assert (
        job.note == "조건 시트: 규칙 5개 · 제외: 적용 안 함 (조건 시트) 1, Advisory (고르지 않음) 1"
        " · 참고 정의 1개"
    )
    assert json.loads(job.context)["10.4"] == ["Required", True]
    page = client.get("/results/1").text
    assert "MISRA C:2012 · 시트 MISRA_Result" in page
    assert '<span class="badge red">Mandatory</span> <b>1</b>' in page
    assert '<span class="badge orange">Required</span> <b>1</b>' in page
    assert "분류: 조건 시트 Required (결과 시트 Advisory)" in page
    assert "함께 보낸 행: 엑셀 3행" in page and "함께 보낸 행: 엑셀 2행" in page
    assert page.count('<pre class="diff">') == 1  # 같은 묶음의 diff는 첫 행에만
    assert "같은 묶음의 수정입니다 (엑셀 2행과 같음)." in page
    assert "MISRA C:2012 <span" in client.get("/results").text

    patch = client.post("/results/1/fixes.patch", data={"row": ["1", "2"]})

    assert patch.status_code == 200  # 같은 수정은 하나로 합쳐 겹침이 아니다
    assert patch.content.count(b"+    return (b != 0) ? a / b : 0;\n") == 1


def test_rte_job_sends_one_function_together(tmp_path):
    reply = answer(("fix", REASON), ("fix", "같은 수정으로 함께 고침"), edits=[DIVIDE_FIX])
    client, _, sent = make_service(tmp_path, reply)
    excel = rte_excel(rte_row("Orange Check"), rte_row("Red Check", line=1, check="Overflow"))

    upload(client, excel, source_folder(tmp_path))

    assert len(sent) == 1  # 같은 함수의 두 행을 한 번에 보냈다
    prompt = sent[0]["messages"][1]["content"]
    assert "Finding 1:\n- TYPE: Red Check\n" in prompt  # 줄 순서: 1번 줄의 Red가 먼저
    assert "Finding 2:\n- TYPE: Orange Check\n" in prompt
    page = client.get("/results/1").text
    assert "함께 보낸 행: 엑셀 3행" in page and "함께 보낸 행: 엑셀 2행" in page
    patch = client.post("/results/1/fixes.patch", data={"row": ["1", "2"]})
    assert patch.status_code == 200  # 같은 수정은 하나로 합쳐 겹침이 아니다


def test_no_fix_row_in_a_mixed_group_does_not_claim_the_shared_fix(tmp_path):
    reply = answer(("fix", REASON), ("no_fix", "직접 고쳐야 합니다."), edits=[DIVIDE_FIX])
    client, _, _ = make_service(tmp_path, reply)
    upload(
        client, MISRA_EXCEL, source_folder(tmp_path), "misra", ["misra:Required", "misra:Mandatory"]
    )

    page = client.get("/results/1").text

    assert page.count('<pre class="diff">') == 1  # 엑셀 2행(fix)의 diff
    assert "같은 묶음의 수정입니다" not in page  # 엑셀 3행은 no_fix라 공유 수정 안내가 없다
    assert "함께 보낸 행: 엑셀 2행" in page and "함께 보낸 행: 엑셀 3행" in page


def test_running_job_can_be_stopped_and_continued(tmp_path):
    queued = []
    client, store, _ = make_service(tmp_path, fix_answer(), submit=queued.append)
    upload(client, EXCEL, source_folder(tmp_path))
    assert 'action="/results/1/stop"' in client.get("/results/1").text

    response = client.post("/results/1/stop", follow_redirects=False)

    assert response.status_code == 303
    assert store.job(1).state == "stopped"
    page = client.get("/results/1").text
    assert "멈춘 작업입니다." in page
    assert ">이어서 처리</button>" in page
    assert "data-refresh=" not in page

    client.post("/results/1/retry")
    for task in list(queued):
        task()

    assert store.job(1).state == "done"


def test_stopped_job_still_gives_a_patch_for_finished_rows(tmp_path):
    queued = []
    client, _, _ = make_service(tmp_path, fix_answer(), submit=queued.append)
    excel = rte_excel(
        rte_row("Orange Check"), rte_row("Orange Check", line=1, check="Overflow", function="other")
    )
    upload(client, excel, source_folder(tmp_path))

    queued[0]()  # 첫 묶음(엑셀 2행)만 처리하고
    client.post("/results/1/stop")  # 멈춘다

    assert 'action="/results/1/fixes.patch"' in client.get("/results/1").text
    response = client.post("/results/1/fixes.patch", data={"row": ["1"]})
    assert response.status_code == 200
    assert b"+    return (b != 0) ? a / b : 0;\n" in response.content


def test_kind_decides_which_fixes_are_picked_automatically(tmp_path, monkeypatch):
    monkeypatch.setitem(KINDS, "rte", replace(RTE, auto_select=lambda f: f.color == "Red"))
    header = {
        "original": "int divide(int a, int b)",
        "replacement": "int divide(int a, int b) /* 검토 */",
    }
    # Red(엑셀 3행)를 먼저 보내므로 첫 답이 Red 몫이다
    client, _, _ = make_service(tmp_path, fix_answer(edits=[header]), fix_answer())
    excel = rte_excel(
        rte_row("Orange Check"), rte_row("Red Check", line=1, check="Overflow", function="other")
    )
    upload(client, excel, source_folder(tmp_path))

    page = client.get("/results/1").text

    assert 'name="row" value="2" data-auto="1"' in page
    assert 'name="row" value="1" data-auto="0"' in page
    assert "자동으로 고르지 않은 수정 1개는 직접 고르세요" in page


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


def test_result_page_has_overview_and_detail_tabs(tmp_path):
    client, _, _ = make_service(tmp_path, fix_answer())
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results/1").text

    assert 'data-tab="overview"' in page and 'data-tab="detail"' in page
    assert "한눈에 보기" in page and "자세히 보기" in page
    assert "검사 종류별" in page and "파일별" in page
    # Division by zero: 전체 2, 수정 1, 수정 불필요 0, 직접 수정 0, 확인 필요 1
    # 긴 이름(MISRA 규칙 문장)은 한 줄로 줄이고 전체는 title로 보인다
    assert (
        '<tr><td title="Division by zero">Division by zero</td><td class="num">2</td>'
        '<td class="num">1</td><td class="num">0</td><td class="num">0</td>'
        '<td class="num">1</td></tr>'
    ) in page
    assert '<td class="mono" title="src/calc.c">src/calc.c</td>' in page
    # 표가 길어져도 상자 안에서 스크롤하고, 아래 내용을 밀어내지 않는다
    assert ".box { max-height:" in page and "text-overflow: ellipsis" in page
    # 상자 안 숫자 열은 좁게 두어 이름 칸을 넓힌다 (머리글은 두 줄로 접힐 수 있다)
    assert ".box td.num, .box th.num { width: 40px;" in page


def test_manual_row_is_counted_separately_and_not_selectable(tmp_path):
    reason = "호출자가 넘기는 값의 범위를 정해야 합니다."
    client, _, _ = make_service(tmp_path, answer(("manual", reason)))
    upload(client, rte_excel(rte_row("Red Check")), source_folder(tmp_path))

    page = client.get("/results/1").text

    assert '<span class="badge orange">직접 수정</span> <b>1</b>' in page  # 맨 위 줄
    assert '<th class="num">직접 수정</th>' in page  # 검사 종류별·파일별 표
    assert (
        '<tr><td title="Division by zero">Division by zero</td><td class="num">1</td>'
        '<td class="num">0</td><td class="num">0</td><td class="num">1</td>'
        '<td class="num">0</td></tr>'
    ) in page
    assert 'data-filter="manual"' in page  # 칩
    assert 'type="checkbox"' not in page  # 고를 diff가 없다
    assert reason in page


def test_row_errors_read_as_what_happened_and_what_to_do(tmp_path):
    wrong = fix_answer(edits=[{"original": "return a % b;", "replacement": "x"}])
    client, _, _ = make_service(tmp_path, wrong, wrong)
    upload(client, EXCEL, source_folder(tmp_path))

    page = client.get("/results/1").text

    assert '<span class="badge amber">확인 필요</span>' in page
    assert "LLM이 고칠 위치를 정확히 짚지 못했습니다." in page
    assert '<div class="todo">할 일: ' in page
    assert "<details><summary>자세히 (개발자용)</summary>" in page
    assert "_job1_row2.md" in page  # 이 행의 대화 기록 파일


def test_conflicting_fixes_are_marked_and_red_is_picked_first(tmp_path):
    other = {"original": "    return a / b;", "replacement": "    return b ? a / b : -1;"}
    client, _, _ = make_service(tmp_path, fix_answer(), fix_answer(edits=[other]))
    excel = rte_excel(
        rte_row("Orange Check"), rte_row("Red Check", check="Overflow", function="other")
    )
    upload(client, excel, source_folder(tmp_path))

    page = client.get("/results/1").text

    # Red(엑셀 3행)가 먼저 골라지고, 같은 줄을 고치는 Orange(엑셀 2행)는 이번엔 제외
    assert 'name="row" value="2" data-auto="1" data-conflicts="1"' in page
    assert 'name="row" value="1" data-auto="0" data-conflicts="2"' in page
    assert "이번엔 제외" in page
    assert "행 3 수정과 같은 줄을 고칩니다" in page


def test_main_profile_uses_its_env_file_and_data_folder(tmp_path, monkeypatch):
    # 같은 폴더에서 서버를 하나 더 띄울 때 설정과 DB·기록이 프로필별로 갈린다
    monkeypatch.chdir(tmp_path)
    for key in (*REQUIRED_VARS, *OPTIONAL_VARS):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env.b").write_text(
        "LLM_BASE_URL=https://llm.test/v1\nLLM_MODEL=alt-model\n", encoding="utf-8"
    )
    workers = []
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.setattr(
        web,
        "Worker",
        lambda *args, **kwargs: workers.append((args, kwargs)) or Worker(*args, **kwargs),
    )

    assert web.main(["--no-browser", "--profile", "b"]) == 0

    ((args, kwargs),) = workers
    assert args[2] == "alt-model"
    assert kwargs["log_dir"] == Path("private/b/llm-logs")
    assert (tmp_path / "private" / "b" / "fixer.db").is_file()
    assert not (tmp_path / "private" / "fixer.db").exists()


def test_main_names_the_missing_profile_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", MODEL)

    assert web.main(["--no-browser", "--profile", "b"]) == 1
    assert ".env.b" in capsys.readouterr().err


USE_C = b'#include "limits.h"\nint divide(int a, int b)\n{\n    return a / (b % LIMIT);\n}\n'


def folder_with_header(tmp_path) -> Path:
    """calc.c가 inc/limits.h의 매크로를 쓰는 소스 폴더. 깨진 파일 하나가 함께 있다."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "inc").mkdir()
    (root / "src" / "calc.c").write_bytes(USE_C)
    (root / "inc" / "limits.h").write_bytes(b"#define LIMIT 4\n#define UNUSED 9\n")
    (root / "src" / "broken.c").write_bytes(b"int a;\r\nint b;\n")
    return root


def test_job_sends_definitions_from_other_files_and_notes_the_count(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))
    excel = rte_excel(rte_row("Orange Check", line=4))

    response = upload(client, excel, folder_with_header(tmp_path))

    assert response.status_code == 303
    content = sent[0]["messages"][1]["content"]
    assert "```c\n// editable: inc/limits.h line 1\n    1| #define LIMIT 4\n```" in content
    assert "UNUSED" not in content
    job = store.job(1)
    # calc.c 자신의 divide 정의도 저장된다(긴 파일에서 함수만 보낼 때 쓴다). 메모는 저장한 수다
    assert "참고 정의 2개" in job.note and "못 읽은 파일 1개" in job.note
    assert [d.name for d in store.definitions(1)] == ["LIMIT", "divide"]


def test_note_counts_each_definition_body_once_even_with_several_names(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))
    root = folder_with_header(tmp_path)
    (root / "inc" / "mode.h").write_bytes(b"typedef enum { MODE_A, MODE_B } mode_t;\n")
    (root / "src" / "calc.c").write_bytes(USE_C.replace(b"% LIMIT", b"% LIMIT + MODE_A + MODE_B"))

    upload(client, rte_excel(rte_row("Orange Check", line=4)), root)

    content = sent[0]["messages"][1]["content"]
    assert content.count("typedef enum { MODE_A, MODE_B } mode_t;") == 1
    assert "참고 정의 3개" in store.job(1).note  # LIMIT, 열거 몸체 1개, divide


def test_definitions_inside_the_sent_code_are_stored_but_not_sent(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer())

    upload(client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path))

    assert [d.name for d in store.definitions(1)] == ["divide"]
    assert "Definitions from other places" not in sent[0]["messages"][1]["content"]
    assert "참고 정의 1개" in store.job(1).note


def test_home_offers_rte_modes_with_guard_checked(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="mode" value="rte:guard" checked' in page
    assert 'name="mode" value="rte:root_cause"' in page and "근본 원인만" in page


def test_job_keeps_the_chosen_mode_and_defaults_to_guard(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer(), fix_answer())

    upload(client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path))
    upload(
        client, rte_excel(rte_row("Orange Check")), source_folder(tmp_path), mode="rte:root_cause"
    )

    assert json.loads(store.job(1).context) == {"mode": "guard"}
    assert store.job(1).note.startswith("가드 허용")
    assert json.loads(store.job(2).context) == {"mode": "root_cause"}
    assert store.job(2).note.startswith("근본 원인만")
    assert "a guard is acceptable" in sent[0]["messages"][0]["content"]
    assert "Never make such a change" in sent[1]["messages"][0]["content"]


def project_with_shared_code(tmp_path) -> tuple[Path, Path]:
    """소스 폴더(proj/app)와, 그 위 폴더의 공용 코드. calc.c는 헤더 별칭으로 공용 함수를 부른다."""
    top = tmp_path / "proj"
    app = top / "app"
    (app / "src").mkdir(parents=True)
    (top / "common").mkdir()
    (app / "src" / "calc.c").write_bytes(
        b'#include "api.h"\nint divide(int a, int b)\n{\n    return a / api_scale(b);\n}\n'
    )
    (top / "common" / "api.h").write_bytes(b"#define api_scale(v) drv_scale(v)\n")
    (top / "common" / "drv.c").write_bytes(b"int drv_scale(int v)\n{\n    return v + 1;\n}\n")
    return app, top


def test_project_folder_supplies_definitions_from_sibling_folders(tmp_path):
    client, store, sent = make_service(tmp_path, fix_answer("no_fix"))
    app, top = project_with_shared_code(tmp_path)
    excel = rte_excel(rte_row("Orange Check", line=4))

    response = upload(client, excel, top)

    assert response.status_code == 303
    content = sent[0]["messages"][1]["content"]
    assert "#define api_scale(v) drv_scale(v)" in content
    assert "int drv_scale(int v)" in content
    assert "common/drv.c" in content and "../" not in content
    assert store.rows(1)[0].file_name == "app/src/calc.c"
    assert "참고 폴더" not in store.job(1).note


def test_home_asks_for_the_whole_project_folder(tmp_path):
    client, _, _ = make_service(tmp_path)

    page = client.get("/").text

    assert 'name="ref_dirs"' not in page
    assert "프로젝트 최상위 폴더" in page


DRV_C = b"int drv_scale(int v)\n{\n    return v + 1;\n}\n"


def test_patch_carries_edits_in_other_files(tmp_path):
    client, store, _ = make_service(tmp_path)
    calc = decode_source("src/calc.c", CALC_C)
    drv = decode_source("lib/drv.c", DRV_C)
    job_id = store.create_job(
        "rte.xlsx",
        "RTE_Result",
        0,
        [calc, drv],
        [NewRow(finding(), "src/calc.c", "pending")],
        source_root=str(tmp_path),
    )
    (row,) = store.rows(job_id)
    local = Edit(CALC_C.index(b"a / b"), CALC_C.index(b"a / b") + 5, "a / (b ? b : 1)")
    start = DRV_C.index(b"v + 1")
    other = Edit(start, start + 5, "v + 2", file="lib/drv.c")
    store.set_row(row.id, "done", FixResult("fix", "r", (local, other), "diff"))

    response = client.post(f"/results/{job_id}/fixes.patch", data={"row": [str(row.id)]})

    assert response.status_code == 200
    assert b"--- a/lib/drv.c\n+++ b/lib/drv.c\n" in response.content
    assert b"+    return v + 2;\n" in response.content
    assert b"--- a/src/calc.c\n+++ b/src/calc.c\n" in response.content


def test_rte_job_stores_definition_files_and_sends_them_editable(tmp_path):
    drv_fix = {
        "file": "common/drv.c",
        "original": "    return v + 1;",
        "replacement": "    return v + 2;",
    }
    client, store, sent = make_service(tmp_path, answer(("fix", REASON), edits=[drv_fix]))
    app, top = project_with_shared_code(tmp_path)

    upload(client, rte_excel(rte_row("Orange Check", line=4)), top)

    assert "common/drv.c" in store.sources(1)
    content = sent[0]["messages"][1]["content"]
    assert "// editable: common/drv.c lines 1-4\n    1| int drv_scale(int v)" in content
    (row,) = store.rows(1)
    assert row.result.decision == "fix"
    assert [edit.file for edit in row.result.edits] == ["common/drv.c"]


def test_misra_job_does_not_store_extra_files(tmp_path):
    client, store, _ = make_service(tmp_path, fix_answer("no_fix"))
    app, top = project_with_shared_code(tmp_path)
    excel = workbook_bytes(
        {"MISRA_Result": [MISRA_HEADER, misra_row(file=r"C:\proj\app\src\calc.c", line=4)]}
    )

    upload(
        client,
        excel,
        top,
        kind="misra",
        choices=("misra:Mandatory", "misra:Required", "misra:Advisory"),
    )

    assert set(store.sources(1)) == {"app/src/calc.c"}
