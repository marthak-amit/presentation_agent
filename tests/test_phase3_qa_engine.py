from __future__ import annotations

import asyncio
from dataclasses import replace

from backend.llm.client import FakeLLM, GroqLLM
from backend.llm.qa import Failed, Filler, Finished, ModelSwitch, QAEngine, Text, ToolUsed
from backend.llm.types import Done, Started, TextDelta, ToolCall

MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "[CONTEXT]c[/CONTEXT][QUESTION]q[/QUESTION]"}]


class Tools:
    def __init__(self):
        self.calls = []

    async def run(self, name, args):
        self.calls.append((name, args))
        return "ok"


class Scripted:
    """LLM stub: behaviour per model name."""
    name = "scripted"

    def __init__(self, behaviours):
        self.b = behaviours
        self.calls = []

    async def complete(self, **kw):
        return ""

    async def stream(self, *, model, messages, tools=None, max_tokens=0, temperature=0):
        self.calls.append(model)
        b = self.b[model]
        if callable(b):
            async for e in b(messages):
                yield e
            return
        raise b


async def good(messages):
    yield Started()
    for w in ["Growth ", "is ", "149 ", "dollars."]:
        yield TextDelta(w)
    yield Done("stop", "m")


class RateLimit(Exception):
    status_code = 429


def settings(s, **kw):
    return replace(s, groq_qa_model="primary", groq_fallback_model="fallback", first_token_filler_s=0.05,
                   first_token_timeout_s=0.2, **kw)


async def collect(engine, tools=None):
    return [e async for e in engine.answer(MSGS, tools or Tools())]


async def test_primary_model_streams(settings):
    s = settings.__class__(**{**settings.__dict__, "groq_qa_model": "primary", "groq_fallback_model": "fallback"})
    llm = Scripted({"primary": good, "fallback": RuntimeError("unused")})
    evs = await collect(QAEngine(llm, s))
    assert "".join(e.text for e in evs if isinstance(e, Text)) == "Growth is 149 dollars."
    fin = evs[-1]
    assert isinstance(fin, Finished) and fin.model == "primary" and not fin.fallback_used and fin.first_token_ms is not None
    assert llm.calls == ["primary"]


async def test_429_falls_back_to_fallback_model(settings):
    s = replace(settings, groq_qa_model="primary", groq_fallback_model="fallback")
    llm = Scripted({"primary": RateLimit("429 too many"), "fallback": good})
    evs = await collect(QAEngine(llm, s))
    assert any(isinstance(e, ModelSwitch) and e.model == "fallback" for e in evs)
    fin = evs[-1]
    assert isinstance(fin, Finished) and fin.model == "fallback" and fin.fallback_used


async def test_slow_first_token_triggers_filler_then_fallback(settings):
    async def slow(messages):
        await asyncio.sleep(5)
        yield Started()

    s = replace(settings, groq_qa_model="primary", groq_fallback_model="fallback", first_token_filler_s=0.05,
                first_token_timeout_s=0.25)
    llm = Scripted({"primary": slow, "fallback": good})
    evs = await collect(QAEngine(llm, s))
    kinds = [type(e).__name__ for e in evs]
    assert kinds.count("Filler") == 1 and kinds.index("Filler") < kinds.index("ModelSwitch")
    assert isinstance(evs[-1], Finished) and evs[-1].model == "fallback"


async def test_all_models_fail_yields_failed(settings):
    s = replace(settings, groq_qa_model="primary", groq_fallback_model="fallback")
    llm = Scripted({"primary": RuntimeError("boom"), "fallback": RateLimit("429")})
    evs = await collect(QAEngine(llm, s))
    assert isinstance(evs[-1], Failed)


async def test_partial_answer_then_error_is_kept(settings):
    async def flaky(messages):
        yield Started()
        yield TextDelta("The price is 149 dollars. ")
        raise RuntimeError("connection reset")

    s = replace(settings, groq_qa_model="primary", groq_fallback_model="fallback")
    evs = await collect(QAEngine(Scripted({"primary": flaky, "fallback": good}), s))
    assert isinstance(evs[-1], Finished) and evs[-1].model == "primary" and "149" in evs[-1].text


async def test_tool_loop_runs_tools_and_continues(settings):
    seen_roles = []

    async def tooly(messages):
        seen_roles.append([m["role"] for m in messages])
        if not any(m["role"] == "tool" for m in messages):
            yield Started()
            yield ToolCall("c1", "goto_slide", {"n": 4})
            yield Done("tool_calls", "m")
        else:
            yield Started()
            yield TextDelta("Here is the traction slide.")
            yield Done("stop", "m")

    tools = Tools()
    s = replace(settings, groq_qa_model="primary", groq_fallback_model="")
    evs = await collect(QAEngine(Scripted({"primary": tooly}), s), tools)
    assert tools.calls == [("goto_slide", {"n": 4})]
    assert any(isinstance(e, ToolUsed) and e.name == "goto_slide" for e in evs)
    assert isinstance(evs[-1], Finished) and "traction" in evs[-1].text
    assert "tool" in seen_roles[1] and "assistant" in seen_roles[1]


async def test_groq_client_parses_stream_and_tool_calls(settings):
    """GroqLLM against a stubbed SDK: content deltas, reasoning deltas ignored, fragmented tool-call args."""
    from types import SimpleNamespace as NS

    def chunk(content=None, tool_calls=None, finish=None, reasoning=None):
        return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls, reasoning=reasoning), finish_reason=finish)])

    chunks = [
        chunk(reasoning="thinking..."),
        chunk(content="Hello "), chunk(content="there."),
        chunk(tool_calls=[NS(index=0, id="c9", function=NS(name="search_kb", arguments='{"que'))]),
        chunk(tool_calls=[NS(index=0, id=None, function=NS(name=None, arguments='ry": "pricing"}'))], finish="tool_calls"),
    ]
    captured = {}

    class Comp:
        async def create(self, **kw):
            captured.update(kw)

            async def gen():
                for c in chunks:
                    yield c

            return gen()

    llm = GroqLLM(replace(settings, groq_reasoning_effort="low", groq_reasoning_model_prefix="openai/gpt-oss"),
                  client=NS(chat=NS(completions=Comp())))
    evs = [e async for e in llm.stream(model="openai/gpt-oss-120b", messages=MSGS, tools=[{"type": "function"}])]
    assert [type(e).__name__ for e in evs] == ["Started", "TextDelta", "TextDelta", "ToolCall", "Done"]
    assert evs[3].name == "search_kb" and evs[3].arguments == {"query": "pricing"}
    assert captured["extra_body"] == {"reasoning_effort": "low", "include_reasoning": False}
    assert captured["stream"] is True and captured["tool_choice"] == "auto"
    captured.clear()
    _ = [e async for e in llm.stream(model="llama-3.1-8b-instant", messages=MSGS)]
    assert "extra_body" not in captured  # reasoning knobs only for the gpt-oss family


async def test_fake_llm_answers_from_context_or_defers(settings):
    ctx = "Slide 5: Pricing\nStarter is 49 dollars a month. Growth is 149 dollars a month."
    llm = FakeLLM("Amit", delay=0)
    msgs = [{"role": "user", "content": f"[CONTEXT]{ctx}[/CONTEXT][QUESTION]how much is the growth plan[/QUESTION]"}]
    out = "".join([e.text async for e in llm.stream(model="m", messages=msgs) if isinstance(e, TextDelta)])
    assert "149" in out
    msgs = [{"role": "user", "content": f"[CONTEXT]{ctx}[/CONTEXT][QUESTION]explain quantum blockchain[/QUESTION]"}]
    out = "".join([e.text async for e in llm.stream(model="m", messages=msgs) if isinstance(e, TextDelta)])
    assert "follow up" in out
