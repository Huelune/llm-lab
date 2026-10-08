import re

from rte_fixtures import finding

from llm_lab.fixer import Exchange
from llm_lab.transcript import render_log, write_log

USER = "Code:\n```c\n    3|     return a / b;\n```"
EXCHANGE = Exchange(
    messages=[{"role": "system", "content": "You review..."}, {"role": "user", "content": USER}],
    raw={"id": "cmpl-1", "choices": []},
    content='{"decision": "fix"}',
    finish_reason="stop",
    prompt_tokens=12,
    completion_tokens=3,
    seconds=1.5,
)


def render(exchanges, outcome="수정 - b가 0일 때를 검사합니다.", extra_body=None):
    return render_log(
        job_id=3,
        finding=finding(),
        source_name="calc.c",
        model="m1",
        endpoint="https://llm.test/v1/chat/completions",
        extra_body=extra_body or {},
        exchanges=exchanges,
        outcome=outcome,
    )


def test_log_shows_row_settings_and_outcome():
    text = render([EXCHANGE], extra_body={"chat_template_kwargs": {"enable_thinking": False}})

    assert text.startswith("# 작업 3 · 엑셀 2행 · Orange Check\n")
    assert "- 위치: calc.c:3 (divide)" in text
    assert "- 모델: m1" in text
    assert "- 보낸 주소: https://llm.test/v1/chat/completions" in text
    assert '- 함께 보낸 값: {"chat_template_kwargs": {"enable_thinking": false}}' in text
    assert "- 결과: 수정 - b가 0일 때를 검사합니다." in text


def test_log_shows_sent_messages_and_reply_verbatim():
    text = render([EXCHANGE, EXCHANGE])

    assert "## 요청 1 (1.5초)" in text
    assert "## 요청 2 (1.5초)" in text
    assert "### 보낸 내용: system" in text
    # 메시지 안의 ``` 코드 블록이 깨지지 않게 더 긴 펜스로 감싼다
    assert f"````text\n{USER}\n````" in text
    assert "### 받은 내용 (finish_reason: stop, 토큰 입력 12 / 출력 3)" in text
    assert '```text\n{"decision": "fix"}\n```' in text
    assert '"id": "cmpl-1"' in text  # 응답 원문 JSON


def test_log_records_errors_and_calls_that_never_happened():
    failed = Exchange(messages=EXCHANGE.messages, seconds=0.2, error="[인증] 인증 실패(401)")

    assert "### 오류\n\n[인증] 인증 실패(401)" in render([failed], outcome="작업 중단")
    reused = render([], outcome="이전 결과 재사용 - LLM을 부르지 않음")
    assert "LLM에 보낸 요청이 없습니다." in reused


def test_write_log_never_overwrites(tmp_path):
    first = write_log(tmp_path / "logs", 3, finding(), "a")
    second = write_log(tmp_path / "logs", 3, finding(), "b")

    assert first != second
    assert first.read_text(encoding="utf-8") == "a"
    assert second.read_text(encoding="utf-8") == "b"
    assert re.fullmatch(r"\d{8}-\d{6}_job3_row2\.md", first.name)
    # 같은 초에 다시 쓰면 -2가 붙고, 초가 바뀌었으면 시각이 다르다
    assert re.fullmatch(r"\d{8}-\d{6}_job3_row2(-2)?\.md", second.name)
