"""llm-probe --parallel: 짧은 요청을 동시에 보내 게이트웨이가 몇 개까지 받는지 잰다.

화면은 캡처해 회사 밖으로 보내므로 개수, 시간, 상태 코드만 찍는다.
주소·모델·키·서버 메시지는 찍지 않는다.
"""

import argparse
import threading
import time

import httpx2
import pytest
from fake_llm import API_KEY, completion, error, make_client

from llm_lab import probe
from llm_lab.config import Settings

# 주소와 모델 이름은 회사 값이라 화면에 나오면 안 된다
SETTINGS = Settings(
    base_url="https://secret-gateway.test/v1",
    api_key=API_KEY,
    model="secret-model",
    project_id="secret-project",
)


def counting(answer):
    """첫 요청(혼자 보내는 기준 요청)은 성공, 그 뒤 동시 요청은 answer(번호)로 답하는 서버."""
    lock = threading.Lock()
    seen = []

    def handler(request):
        with lock:
            seen.append(request)
            number = len(seen)
        return completion("pong") if number == 1 else answer(number)

    return handler, seen


def run(handler, capsys, count=4):
    code = probe.run_parallel(make_client(handler), SETTINGS, count)
    return code, capsys.readouterr().out


def test_requests_really_go_out_together_and_all_succeed(capsys):
    barrier = threading.Barrier(4, timeout=5)  # 4개가 동시에 와야 풀린다

    def answer(number):
        barrier.wait()
        return completion("pong")

    handler, seen = counting(answer)
    code, out = run(handler, capsys)

    assert code == 0
    assert len(seen) == 5  # 기준 1개 + 동시 4개
    assert "혼자 1개: 성공" in out
    assert "동시 4개: 성공 4 / 429 0 / 그 밖의 오류 0" in out
    assert "LLM_WORKERS=4" in out
    assert "secret" not in out and API_KEY not in out


def test_a_gateway_that_queues_requests_is_called_out(capsys):
    def answer(number):
        time.sleep(1.2)  # 혼자일 때는 바로, 동시일 때는 줄을 서서 늦게 온다
        return completion("pong")

    handler, _ = counting(answer)
    code, out = run(handler, capsys, count=2)

    assert code == 0
    assert "동시 2개: 성공 2 / 429 0 / 그 밖의 오류 0" in out
    assert "줄을 세우는 것으로 보여" in out
    assert "LLM_WORKERS=2까지" not in out


def test_too_many_requests_are_counted_and_the_verdict_says_fewer(capsys):
    def answer(number):
        if number % 2:
            return error(429, "secret gateway detail: limit 2 per project")
        return completion("pong")

    handler, _ = counting(answer)
    code, out = run(handler, capsys)

    assert code == 0
    assert "동시 4개: 성공 2 / 429 2 / 그 밖의 오류 0" in out
    assert "오류 종류: 429 2개" in out
    assert "4개보다 적게" in out
    assert "secret" not in out


def test_other_errors_are_shown_by_kind_only(capsys):
    def answer(number):
        if number == 2:
            return error(503, "secret upstream down")
        if number == 3:
            raise httpx2.ReadTimeout("secret timed out", request=httpx2.Request("POST", "x"))
        return completion("pong")

    handler, _ = counting(answer)
    code, out = run(handler, capsys)

    assert "동시 4개: 성공 2 / 429 0 / 그 밖의 오류 2" in out
    assert "503 1개" in out and "시간 초과 1개" in out
    assert "secret" not in out


def test_stops_when_a_single_request_already_fails(capsys):
    seen = []

    def handler(request):
        seen.append(request)
        return error(401, "secret bad key")

    code, out = run(handler, capsys)

    assert code == 1
    assert len(seen) == 1  # 동시 요청은 보내지 않는다
    assert "혼자 1개부터 실패: 401" in out
    assert "secret" not in out


@pytest.mark.parametrize("text", ["0", "17", "abc"])
def test_parallel_count_must_be_between_1_and_16(text):
    with pytest.raises(argparse.ArgumentTypeError, match="1~16"):
        probe.parallel_count(text)


def test_parallel_count_accepts_the_range():
    assert probe.parallel_count("1") == 1
    assert probe.parallel_count("16") == 16
