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


def render(
    exchanges, outcomes=("수정 - b가 0일 때를 검사합니다.",), extra_body=None, findings=None
):
    return render_log(
        job_id="3",
        kind="RTE",
        findings=findings or [finding()],
        source_name="calc.c",
        model="m1",
        endpoint="https://llm.test/v1/chat/completions",
        extra_body=extra_body or {},
        exchanges=exchanges,
        outcomes=list(outcomes),
    )


def test_log_shows_row_settings_and_outcome():
    text = render([EXCHANGE], extra_body={"chat_template_kwargs": {"enable_thinking": False}})

    assert text.startswith("# 작업 3 · RTE · 엑셀 2행\n")
    assert (
        "- 엑셀 2행: Division by zero / Warning: scalar division by zero may occur"
        " / calc.c:3 (divide)"
    ) in text
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


def test_log_for_a_group_lists_each_row_and_outcome():
    findings = [finding(), finding(row=4, line=None, function="", check="Overflow")]
    outcomes = ["엑셀 2행 수정 - 고침", "엑셀 4행 수정 불필요 - 그대로"]

    text = render([EXCHANGE], outcomes=outcomes, findings=findings)

    assert text.startswith("# 작업 3 · RTE · 엑셀 2·4행\n")
    assert "- 엑셀 4행: Overflow / Warning: scalar division by zero may occur / calc.c:?\n" in text
    assert "- 결과: 엑셀 2행 수정 - 고침\n- 결과: 엑셀 4행 수정 불필요 - 그대로\n" in text


def test_log_records_errors_and_calls_that_never_happened():
    failed = Exchange(messages=EXCHANGE.messages, seconds=0.2, error="[인증] 인증 실패(401)")

    assert "### 오류\n\n[인증] 인증 실패(401)" in render([failed], outcomes=["작업 중단"])
    reused = render([], outcomes=["이전 결과 재사용 - LLM을 부르지 않음"])
    assert "LLM에 보낸 요청이 없습니다." in reused


JOB = "0f8fad5b-d9cb-469f-a165-70867728950e"


def test_write_log_never_overwrites(tmp_path):
    first = write_log(tmp_path / "logs", JOB, 2, "a")
    second = write_log(tmp_path / "logs", JOB, 2, "b")

    assert first != second
    assert first.read_text(encoding="utf-8") == "a"
    assert second.read_text(encoding="utf-8") == "b"
    assert re.fullmatch(rf"\d{{8}}-\d{{6}}_job{JOB}_row2\.md", first.name)
    # 같은 초에 다시 쓰면 -2가 붙고, 초가 바뀌었으면 시각이 다르다
    assert re.fullmatch(rf"\d{{8}}-\d{{6}}_job{JOB}_row2(-2)?\.md", second.name)
