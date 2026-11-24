"""Model access behind one small interface.

Nothing else in the codebase imports the SDK. The agent loop calls `step()`
once per turn and gets back the assistant content (to append to history
unchanged) plus any tool calls. `ScriptedClient` replays a fixed sequence of
turns, for tests and the free demo mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, Union

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Step:
    content: list[Any]  # assistant content blocks, appended to history as-is
    tool_calls: list[ToolCall]
    text: str
    stop_reason: str


class ModelClient(Protocol):
    def step(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Step: ...


class ModelCallError(RuntimeError):
    pass


ScriptedTurn = Union[str, list[tuple[str, dict[str, Any]]]]


@dataclass
class ScriptedClient:
    """Replays turns in order. A turn is either final text, or a list of
    (tool_name, input) calls made in parallel. Records what it was shown."""

    turns: list[ScriptedTurn]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def step(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Step:
        self.calls.append({"n_messages": len(messages), "last": messages[-1]})
        if not self.turns:
            raise ModelCallError("ScriptedClient has no turns left")
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return Step([{"type": "text", "text": turn}], [], turn, "end_turn")
        n = len(self.calls)
        calls = [ToolCall(f"toolu_s{n:02d}_{i}", name, inp) for i, (name, inp) in enumerate(turn)]
        content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        return Step(content, calls, "", "tool_use")


class AnthropicClient:
    """Messages API tool-use turn. Thinking is adaptive (the default on this
    model); server-side fallbacks re-run a refused request on another model."""

    def __init__(self, model: str | None = None, effort: str | None = None,
                 max_tokens: int = 16000, client: Any = None):
        self.model = model or os.environ.get("BUYER_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("BUYER_EFFORT", DEFAULT_EFFORT)
        self.max_tokens = max_tokens
        self.usage: list[Any] = []
        if client is None:
            import anthropic  # the only place the SDK is imported

            client = anthropic.Anthropic()
        self._client = client

    def step(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Step:
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        self.usage.append(getattr(response, "usage", None))
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            raise ModelCallError(f"model declined the request ({getattr(details, 'category', None)})")
        if stop == "max_tokens":
            raise ModelCallError("model hit max_tokens before finishing its turn")
        content = list(response.content)
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in content if getattr(b, "type", None) == "tool_use"]
        text = "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()
        return Step(content, calls, text, stop or "end_turn")
