"""LLM과 오간 대화를 사람이 읽는 Markdown 파일로 남긴다."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_lab.fixer import Exchange
from llm_lab.polyspace import Finding


def _fence(text: str, lang: str = "text") -> str:
    """text 안의 백틱보다 긴 펜스로 감싼다 (메시지 속 ``` 코드 블록이 깨지지 않게)."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def render_log(
    *,
    job_id: int,
    finding: Finding,
    source_name: str,
    model: str,
    endpoint: str,
    extra_body: Mapping[str, Any],
    exchanges: list[Exchange],
    outcome: str,
) -> str:
    """행 하나의 처리 기록: 행 정보, 보낸 메시지와 받은 응답을 그대로 적는다."""
    where = f"{source_name}:{finding.line if finding.line is not None else '?'}"
    if finding.function:
        where += f" ({finding.function})"
    lines = [
        f"# 작업 {job_id} · 엑셀 {finding.row}행 · {finding.type}",
        "",
        f"- 기록 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- check: {finding.check}",
        f"- detail: {finding.detail}",
        f"- 위치: {where}",
        f"- 모델: {model}",
        f"- 보낸 주소: {endpoint}",
    ]
    if extra_body:
        lines.append(f"- 함께 보낸 값: {json.dumps(extra_body, ensure_ascii=False)}")
    lines.append(f"- 결과: {outcome}")
    if not exchanges:
        lines += ["", "LLM에 보낸 요청이 없습니다."]
    for number, exchange in enumerate(exchanges, start=1):
        lines += ["", f"## 요청 {number} ({exchange.seconds:.1f}초)"]
        for message in exchange.messages:
            lines += ["", f"### 보낸 내용: {message['role']}", "", _fence(message["content"])]
        if exchange.error:
            lines += ["", "### 오류", "", exchange.error]
            continue
        if exchange.prompt_tokens is None:
            tokens = "토큰 정보 없음"
        else:
            tokens = f"토큰 입력 {exchange.prompt_tokens} / 출력 {exchange.completion_tokens}"
        raw = json.dumps(exchange.raw, ensure_ascii=False, indent=2)
        lines += [
            "",
            f"### 받은 내용 (finish_reason: {exchange.finish_reason}, {tokens})",
            "",
            _fence(exchange.content),
            "",
            "<details><summary>응답 원문 JSON</summary>",
            "",
            _fence(raw, "json"),
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def write_log(folder: Path, job_id: int, finding: Finding, text: str) -> Path:
    """시각_job작업_row엑셀행.md로 저장한다. 같은 이름이 있으면 -2, -3을 붙여 덮어쓰지 않는다."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}_job{job_id}_row{finding.row}"
    path = folder / f"{stem}.md"
    number = 2
    while path.exists():
        path = folder / f"{stem}-{number}.md"
        number += 1
    path.write_text(text, encoding="utf-8")
    return path
