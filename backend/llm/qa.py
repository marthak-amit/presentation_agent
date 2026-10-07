"""Streaming Q&A engine: tool loop + fallback chain (QA model -> fallback model -> canned follow-up).

Fallback triggers: error / 429 / no first token within `first_token_timeout_s`.
A filler is requested (once) if no first token within `first_token_filler_s` of the question ending.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from ..config import Settings
from .client import LLMClient
from .tools import TOOLS
from .types import Started, TextDelta, ToolCall

log = logging.getLogger("llm.qa")

MAX_TOOL_ROUNDS = 3
STALL_TIMEOUT_S = 8.0


class Toolbox(Protocol):
    async def run(self, name: str, args: dict) -> str: ...


# ---- events
@dataclass
class Filler:
    pass


@dataclass
class Text:
    text: str


@dataclass
class ToolUsed:
    name: str
    args: dict
    result: str


@dataclass
class ModelSwitch:
    model: str
    reason: str


@dataclass
class Finished:
    model: str
    first_token_ms: float | None
    total_ms: float
    text: str
    fallback_used: bool


@dataclass
class Failed:
    reason: str
    first_token_ms: float | None
    total_ms: float


QAEvent = Filler | Text | ToolUsed | ModelSwitch | Finished | Failed


class AttemptFailed(Exception):
    pass


class QAEngine:
    def __init__(self, llm: LLMClient, settings: Settings, clock=time.monotonic):
        self.llm = llm
        self.s = settings
        self.clock = clock

    async def answer(self, messages: list[dict], toolbox: Toolbox, *, t0: float | None = None,
                     models: list[str] | None = None) -> AsyncIterator[QAEvent]:
        t0 = self.clock() if t0 is None else t0
        models = models if models is not None else (self.s.qa_models() or [""])
        st = {"filler": False, "first_token_ms": None, "text": ""}
        for idx, model in enumerate(models):
            try:
                async for ev in self._attempt(model, messages, toolbox, t0, st):
                    yield ev
                if not st["text"].strip():
                    raise AttemptFailed("empty answer")
                yield Finished(model, st["first_token_ms"], (self.clock() - t0) * 1000, st["text"], idx > 0)
                return
            except AttemptFailed as e:
                log.warning("QA model %r failed: %s", model, e)
                if idx + 1 < len(models):
                    yield ModelSwitch(models[idx + 1], str(e))
        yield Failed("all models failed", st["first_token_ms"], (self.clock() - t0) * 1000)

    async def _attempt(self, model: str, messages: list[dict], toolbox: Toolbox, t0: float, st: dict):
        q: asyncio.Queue = asyncio.Queue()
        start = self.clock()

        async def pump():
            try:
                msgs = list(messages)
                for rnd in range(MAX_TOOL_ROUNDS):
                    calls: list[ToolCall] = []
                    round_text = ""
                    tools = TOOLS if rnd < MAX_TOOL_ROUNDS - 1 else None
                    async for ev in self.llm.stream(model=model, messages=msgs, tools=tools, max_tokens=900,
                                                    temperature=0.3):
                        if isinstance(ev, Started):
                            q.put_nowait(("started",))
                        elif isinstance(ev, TextDelta):
                            round_text += ev.text
                            q.put_nowait(("text", ev.text))
                        elif isinstance(ev, ToolCall):
                            calls.append(ev)
                    if not calls:
                        break
                    msgs.append({"role": "assistant", "content": round_text or None, "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls]})
                    for c in calls:
                        try:
                            result = await toolbox.run(c.name, c.arguments)
                        except Exception as e:  # a broken tool must not kill the answer
                            result = f"tool error: {e}"
                        q.put_nowait(("tool", c.name, c.arguments, result))
                        msgs.append({"role": "tool", "tool_call_id": c.id, "content": result})
                q.put_nowait(("done",))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                q.put_nowait(("error", e))

        task = asyncio.create_task(pump(), name="qa-pump")
        started = False
        try:
            while True:
                now = self.clock()
                if not started:
                    deadlines = [self.s.first_token_timeout_s - (now - start)]
                    if not st["filler"]:
                        deadlines.append(self.s.first_token_filler_s - (now - t0))
                    timeout = max(0.0, min(deadlines))
                else:
                    timeout = STALL_TIMEOUT_S
                try:
                    item = await asyncio.wait_for(q.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    now = self.clock()
                    if started:
                        if st["text"].strip():
                            return  # stalled mid-answer: say what we have
                        raise AttemptFailed("stalled")
                    if not st["filler"] and now - t0 >= self.s.first_token_filler_s:
                        st["filler"] = True
                        yield Filler()
                    if now - start >= self.s.first_token_timeout_s:
                        raise AttemptFailed(f"no first token in {self.s.first_token_timeout_s}s")
                    continue
                kind = item[0]
                if kind == "started":
                    if not started:
                        started = True
                        if st["first_token_ms"] is None:
                            st["first_token_ms"] = (self.clock() - t0) * 1000
                elif kind == "text":
                    started = True
                    if st["first_token_ms"] is None:
                        st["first_token_ms"] = (self.clock() - t0) * 1000
                    st["text"] += item[1]
                    yield Text(item[1])
                elif kind == "tool":
                    yield ToolUsed(item[1], item[2], item[3])
                elif kind == "done":
                    return
                elif kind == "error":
                    if st["text"].strip():
                        log.warning("QA stream error after partial answer: %s", item[1])
                        return
                    raise AttemptFailed(f"{type(item[1]).__name__}: {item[1]}")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
