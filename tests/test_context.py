from fake_llm import API_KEY, MODEL, completion, error, make_client, request_json

from llm_lab import probe
from llm_lab.config import Settings

SETTINGS = Settings(base_url="https://llm.test/v1", api_key=API_KEY, model=MODEL)
VLLM_LIMIT_MESSAGE = (
    "litellm.ContextWindowExceededError: litellm.BadRequestError: Hosted_vllmException - "
    "This model's maximum context length is 20000 tokens. However, you requested 32001 tokens "
    "(32000 in the messages, 1 in the completion). Please reduce the length of the messages."
)


def limited_server(limit: int):
    sent: list[dict] = []

    def handler(request):
        body = request_json(request)
        sent.append(body)
        words = len(body["messages"][0]["content"].split())
        return completion("x") if words <= limit else error(400, VLLM_LIMIT_MESSAGE)

    return handler, sent


def test_context_grows_until_rejected_and_shows_server_message(capsys):
    handler, sent = limited_server(20_000)

    code = probe.run_context(make_client(handler), SETTINGS)

    out = capsys.readouterr().out
    assert code == 0
    assert [len(body["messages"][0]["content"].split()) for body in sent] == [
        8_000,
        16_000,
        32_000,
    ]
    assert all(body["max_tokens"] == 1 for body in sent)  # 생성은 1토큰만
    assert "maximum context length is 20000 tokens" in out  # 긴 게이트웨이 메시지도 잘리지 않게
    assert "16,000토큰은 통과, 32,000토큰은 거부" in out


def test_context_switches_to_max_completion_tokens_when_server_asks(capsys):
    # 추론형 모델(OpenAI·Azure)은 max_tokens를 거부하고 max_completion_tokens를 요구한다
    sent: list[dict] = []

    def handler(request):
        body = request_json(request)
        sent.append(body)
        if "max_tokens" in body:
            return error(
                400,
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.",
            )
        words = len(body["messages"][0]["content"].split())
        return completion("x") if words <= 20_000 else error(400, VLLM_LIMIT_MESSAGE)

    code = probe.run_context(make_client(handler), SETTINGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "max_tokens" in sent[0]  # 처음에는 max_tokens로 보내 보고
    assert all(body.get("max_completion_tokens") == 1 for body in sent[1:])  # 이후로는 바꿔 보낸다
    assert "Unsupported parameter" not in out
    assert "16,000토큰은 통과, 32,000토큰은 거부" in out


def test_context_reports_lower_bound_when_everything_passes(capsys):
    handler, sent = limited_server(10**9)

    code = probe.run_context(make_client(handler), SETTINGS)

    assert code == 0
    assert len(sent) == len(probe.CONTEXT_SIZES)
    assert f"{probe.CONTEXT_SIZES[-1]:,}토큰 이상" in capsys.readouterr().out


def test_context_rejected_at_smallest_size_exits_nonzero(capsys):
    def handler(request):
        return error(413, "Request Entity Too Large")

    code = probe.run_context(make_client(handler), SETTINGS)

    out = capsys.readouterr().out
    assert code == 1
    assert "413" in out
    assert "Request Entity Too Large" in out
