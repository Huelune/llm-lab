import subprocess
import sys
from pathlib import Path

import pytest

from llm_lab import folder_picker
from llm_lab.folder_picker import PICK_SCRIPT, PickerError, pick_folder


def fake_run(stdout: str, stderr: str = "", calls: list | None = None):
    def run(args, **kwargs):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=stderr)

    return run


def test_returns_chosen_folder_as_windows_path(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        folder_picker.subprocess, "run", fake_run('{"path": "C:/work/brake/src"}\n', calls=calls)
    )

    assert pick_folder(r"C:\work") == str(Path("C:/work/brake/src"))
    # 서버와 분리된 프로세스에서 같은 Python으로 창을 띄우고, 처음 보여줄 폴더를 넘긴다
    assert calls == [[sys.executable, "-c", PICK_SCRIPT, r"C:\work"]]


def test_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run('{"path": null}\n'))

    assert pick_folder() is None


def test_missing_tkinter_is_explained(monkeypatch):
    message = '{"error": "이 PC의 Python에는 폴더 선택 창(tkinter)이 없습니다"}'
    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run(message))

    with pytest.raises(PickerError, match="tkinter"):
        pick_folder()


def test_crash_shows_the_end_of_stderr(monkeypatch):
    monkeypatch.setattr(
        folder_picker.subprocess, "run", fake_run("", stderr="Traceback ...\nTclError: no display")
    )

    with pytest.raises(PickerError, match="TclError: no display"):
        pick_folder()


def test_window_left_open_too_long_gives_up(monkeypatch):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(folder_picker.subprocess, "run", run)

    with pytest.raises(PickerError, match="응답이 없어"):
        pick_folder()


def test_picker_script_is_valid_python():
    compile(PICK_SCRIPT, "<folder picker>", "exec")
