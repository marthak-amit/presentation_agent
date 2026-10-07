"""Events emitted by an LLM stream (provider-neutral)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Started:
    """First content / tool-call delta received (reasoning deltas don't count)."""


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Done:
    finish_reason: str | None = None
    model: str = ""


LLMEvent = Started | TextDelta | ToolCall | Done
