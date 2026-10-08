"""윈도우 폴더 선택 창. 서비스가 사용자와 같은 PC에서 돌 때만 쓴다.

창은 서버와 분리된 프로세스에서 띄운다. 창(Tk) 문제가 생겨도 서버가 멈추지 않게 하고,
창이 쓰는 스레드 제약을 서버 스레드와 섞지 않기 위해서다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 창을 오래 열어 두면 포기하는 시간(초)
PICK_TIMEOUT = 600.0

# 결과는 json(ASCII)으로 찍어 콘솔 인코딩과 상관없이 한글 경로를 주고받는다
PICK_SCRIPT = r"""
import json
import sys

try:
    import tkinter
    from tkinter import filedialog
except ImportError:
    print(json.dumps({"error": "이 PC의 Python에는 폴더 선택 창(tkinter)이 없습니다"}))
    sys.exit(0)

root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)  # 브라우저 뒤에 숨지 않게
root.lift()
root.focus_force()
initial = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
path = filedialog.askdirectory(
    parent=root, title="소스 폴더 선택", initialdir=initial, mustexist=True
)
root.destroy()
print(json.dumps({"path": path or None}))
"""


class PickerError(Exception):
    """폴더 선택 창을 띄우지 못함."""


def pick_folder(initial: str = "") -> str | None:
    """폴더 선택 창을 띄워 고른 폴더 경로를, 취소하면 None을 돌려준다."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", PICK_SCRIPT, initial],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PICK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise PickerError("폴더 선택 창이 오래 응답이 없어 닫았습니다. 다시 눌러 주세요") from None
    except OSError as exc:
        raise PickerError(f"폴더 선택 창을 띄우지 못했습니다: {exc}") from exc
    lines = done.stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1])
    except IndexError, json.JSONDecodeError:
        detail = done.stderr.strip()[-300:] or "응답 없음"
        raise PickerError(f"폴더 선택 창을 띄우지 못했습니다: {detail}") from None
    if data.get("error"):
        raise PickerError(data["error"])
    path = data.get("path")
    # Tk는 C:/work/src처럼 /로 돌려주므로 윈도우 표기로 바꾼다
    return str(Path(path)) if path else None
