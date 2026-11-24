from types import SimpleNamespace as NS

import pytest

from buyer.agent import TOOLS
from buyer.model import AnthropicClient, ModelCallError


class FakeSDK:
    def __init__(self, response):
        self.response, self.kwargs = response, None
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def reply(stop="tool_use", content=None):
    return NS(stop_reason=stop, stop_details=None, usage=NS(input_tokens=1, output_tokens=1),
              content=content or [])


def test_request_shape(monkeypatch):
    monkeypatch.delenv("BUYER_MODEL", raising=False)
    monkeypatch.delenv("BUYER_EFFORT", raising=False)
    sdk = FakeSDK(reply("end_turn", [NS(type="text", text="hi")]))
    step = AnthropicClient(client=sdk).step("sys", [{"role": "user", "content": "x"}], TOOLS)
    k = sdk.kwargs
    assert k["model"] == "claude-opus-5-5"
    assert k["output_config"] == {"effort": "high"}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["tools"] is TOOLS and "thinking" not in k and "tool_choice" not in k
    assert step.text == "hi" and step.tool_calls == []


def test_parses_tool_calls_and_keeps_content_for_history():
    thinking = NS(type="thinking", thinking="", signature="sig")
    call = NS(type="tool_use", id="toolu_1", name="get_plan", input={})
    step = AnthropicClient(client=FakeSDK(reply("tool_use", [thinking, call]))).step("s", [], TOOLS)
    assert step.content == [thinking, call]  # thinking blocks go back unchanged
    assert [(c.id, c.name) for c in step.tool_calls] == [("toolu_1", "get_plan")]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_unusable_stops_raise(stop):
    with pytest.raises(ModelCallError):
        AnthropicClient(client=FakeSDK(reply(stop))).step("s", [], TOOLS)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("BUYER_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("BUYER_EFFORT", "medium")
    c = AnthropicClient(client=FakeSDK(reply()))
    assert (c.model, c.effort) == ("claude-sonnet-5-5", "medium")
