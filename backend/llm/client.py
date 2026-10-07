"""LLM client interface, Groq implementation and an offline fake."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Protocol

from ..config import Settings
from .types import Done, LLMEvent, Started, TextDelta, ToolCall

log = logging.getLogger("llm")


class LLMClient(Protocol):
    name: str

    async def complete(
        self, *, model: str, messages: list[dict], max_tokens: int = 1200, temperature: float = 0.6
    ) -> str: ...

    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.3,
    ) -> AsyncIterator[LLMEvent]: ...


# --------------------------------------------------------------------------- Groq
class GroqLLM:
    name = "groq"

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        if client is None:
            from groq import AsyncGroq

            client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0, timeout=30.0)
        self._client = client

    def _extra(self, model: str) -> dict:
        s = self.settings
        if s.groq_reasoning_effort and s.groq_reasoning_model_prefix and model.startswith(s.groq_reasoning_model_prefix):
            return {"reasoning_effort": s.groq_reasoning_effort, "include_reasoning": False}
        return {}

    async def _create(self, kwargs: dict):
        extra = self._extra(kwargs["model"])
        try:
            return await self._client.chat.completions.create(**kwargs, **({"extra_body": extra} if extra else {}))
        except Exception as e:  # reasoning knobs unsupported -> retry once without them
            if extra and type(e).__name__ in {"BadRequestError", "UnprocessableEntityError"}:
                log.warning("retrying %s without reasoning extras: %s", kwargs["model"], e)
                return await self._client.chat.completions.create(**kwargs)
            raise

    async def warm(self) -> None:
        """Open the TLS connection ahead of the first question (saves ~100-300 ms on the first answer)."""
        try:
            await self._client.models.list()
        except Exception as e:
            log.debug("groq warm-up failed: %s", e)

    async def complete(self, *, model, messages, max_tokens=1200, temperature=0.6) -> str:
        resp = await self._create(
            dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=False)
        )
        return (resp.choices[0].message.content or "").strip()

    async def stream(self, *, model, messages, tools=None, max_tokens=1200, temperature=0.3):
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = await self._create(kwargs)
        started = False
        calls: dict[int, dict] = {}
        finish = None
        async for chunk in resp:
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            text = getattr(delta, "content", None)
            if text:
                if not started:
                    started = True
                    yield Started()
                yield TextDelta(text)
            for tc in getattr(delta, "tool_calls", None) or []:
                if not started:
                    started = True
                    yield Started()
                slot = calls.setdefault(tc.index or 0, {"id": "", "name": "", "args": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments
            if choice.finish_reason:
                finish = choice.finish_reason
        for idx in sorted(calls):
            c = calls[idx]
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
            except json.JSONDecodeError:
                args = {}
            yield ToolCall(id=c["id"] or f"call_{idx}", name=c["name"], arguments=args)
        yield Done(finish_reason=finish, model=model)


# --------------------------------------------------------------------------- Fake
def _tag(text: str, name: str) -> str:
    m = re.search(rf"\[{name}\](.*?)\[/{name}\]", text, re.S)
    return m.group(1).strip() if m else ""


_WORD = re.compile(r"[a-z0-9']+")
_STOP = set("a an the is are was were to of and or in on for with what how why when who which do does did you your it this that be can could would about me i we our they their there here from at as by".split())


def _kw(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


class FakeLLM:
    """Deterministic offline stand-in so the whole pipeline runs without GROQ_API_KEY."""

    name = "fake"

    def __init__(self, presenter: str = "Presenter", delay: float = 0.004):
        self.presenter = presenter
        self.delay = delay

    # narration ------------------------------------------------------------
    async def complete(self, *, model, messages, max_tokens=1200, temperature=0.6) -> str:
        user = messages[-1]["content"]
        title = _tag(user, "TITLE")
        notes = _tag(user, "NOTES")
        body = _tag(user, "BODY")
        pos = _tag(user, "POSITION")
        nxt = _tag(user, "NEXT_TITLE")
        presenter = _tag(user, "PRESENTER") or self.presenter
        n, _, total = pos.partition("/")
        src = notes if notes and notes != "(none)" else body
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", src.replace("(none)", "")) if s.strip()]
        sentences = [s.lstrip("-*• ").rstrip() for s in sentences]
        sentences = [s if s[-1:] in ".!?" else s + "." for s in sentences]
        out: list[str] = []
        if n == "1":
            out.append(f"Hi everyone, I'm {presenter}, and I'm glad to be walking you through this today.")
        if title and title != "(none)":
            out.append(f"Let's talk about {title.rstrip('.')}.")
        out.extend(sentences[:10])
        if n == total:
            out.append("That brings me to the end, and I'd love to take your questions now.")
        elif nxt and not nxt.startswith("("):
            out.append(f"With that in mind, let's move on to {nxt.rstrip('.')}.")
        return " ".join(out)

    # Q&A --------------------------------------------------------------------
    async def stream(self, *, model, messages, tools=None, max_tokens=1200, temperature=0.3):
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        question = _tag(user, "QUESTION")
        context = _tag(user, "CONTEXT")
        had_tool_result = any(m["role"] == "tool" for m in messages)
        yield Started()
        m = re.search(r"slide\s+(\d+)", question.lower())
        if tools and m and not had_tool_result and any(t["function"]["name"] == "goto_slide" for t in tools):
            yield ToolCall(id="call_fake_1", name="goto_slide", arguments={"n": int(m.group(1))})
            yield Done("tool_calls", model)
            return
        answer = self._answer(question, context)
        for w in answer.split(" "):
            await asyncio.sleep(self.delay)
            yield TextDelta(w + " ")
        yield Done("stop", model)

    def _answer(self, question: str, context: str) -> str:
        qk = _kw(question)
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", context) if len(s.strip()) > 20]
        scored = sorted(((len(qk & _kw(s)), -i, s) for i, s in enumerate(sents)), reverse=True)
        best = [s for score, _, s in scored if score > 0][:2]
        if not best:
            return f"I don't have that detail in front of me, so I'll have {self.presenter} follow up on that after the session."
        return " ".join(s if s[-1] in ".!?" else s + "." for s in best)


def make_llm(settings: Settings) -> LLMClient:
    if settings.use_real_llm:
        return GroqLLM(settings)
    log.warning("GROQ_API_KEY missing -> using FakeLLM (mock)")
    return FakeLLM(settings.presenter_name)
