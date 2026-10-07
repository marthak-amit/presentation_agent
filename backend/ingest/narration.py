"""Narration generation: one sequential Groq call per slide, 429-aware, never blocks the pipeline."""
from __future__ import annotations

import logging

from ..config import Settings
from ..llm.client import FakeLLM, LLMClient
from ..llm.prompts import SlideBrief, narration_messages
from ..llm.retry import with_backoff
from .sentences import clean_narration, split_sentences

log = logging.getLogger("ingest.narration")


async def generate_slide_narration(llm: LLMClient, settings: Settings, brief: SlideBrief, tone: str = "conversational",
                                   length: str = "standard", instruction: str = "", previous: str = "") -> tuple[str, str]:
    """Returns (text, model_used). Tries script model, then fallback model, then a deterministic script."""
    msgs = narration_messages(settings.presenter_name, brief, tone, length, instruction, previous)
    for model in settings.script_models() or [""]:
        try:
            text = await with_backoff(
                lambda m=model: llm.complete(model=m, messages=msgs, max_tokens=1500, temperature=0.6)
            )
            text = clean_narration(text)
            if len(text.split()) >= 15:
                return text, model or llm.name
            log.warning("slide %s: model %s returned too little text", brief.n, model)
        except Exception as e:
            log.warning("slide %s: narration via %s failed: %s", brief.n, model, e)
    text = await FakeLLM(settings.presenter_name).complete(model="", messages=msgs)
    return clean_narration(text), "fallback-template"


def brief_for(slides: list[dict], idx: int) -> SlideBrief:
    s = slides[idx]
    nxt = slides[idx + 1] if idx + 1 < len(slides) else None
    return SlideBrief(n=s["n"], total=len(slides), title=s["title"], body=s["body"], tables=s["tables"], notes=s["notes"],
                      next_title=nxt["title"] if nxt else "", next_body=(nxt["body"][:200] if nxt else ""))


async def build_narration(llm: LLMClient, settings: Settings, slides: list[dict], progress=None,
                          tone: str = "conversational", length: str = "standard") -> list[dict]:
    total = len(slides)
    out: list[dict] = []
    for idx, s in enumerate(slides):
        text, model = await generate_slide_narration(llm, settings, brief_for(slides, idx), tone, length)
        sentences = split_sentences(text)
        out.append({"n": s["n"], "title": s["title"], "sentences": sentences, "model": model})
        if progress:
            progress(idx + 1, total)
    return out
