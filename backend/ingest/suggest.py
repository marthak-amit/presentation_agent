"""Likely audience questions for a deck (shown as click-to-ask chips; also a demo fallback when the room is noisy)."""
from __future__ import annotations

import logging
import re

from ..llm.retry import with_backoff

log = logging.getLogger("ingest.suggest")

PROMPT = """You help a presenter prepare for Q&A. From the material below, write {n} short, natural questions an audience
member could ask AFTER this talk. Each must be answerable from the material, spoken-style (max 14 words), one per line,
no numbering, no quotes, each ending with a question mark. Mix: numbers/facts, "how does it work", "why", and one skeptical question.

MATERIAL:
{material}
"""


def heuristic(slides: list[dict], n: int = 6) -> list[str]:
    out: list[str] = []
    for s in slides[1:] or slides:
        t = re.sub(r"\s+", " ", s.get("title", "")).strip(" .:")
        if t and not t.lower().startswith("slide "):
            out.append(f"Can you explain {t} in more detail?")
    return out[:n]


def clean_questions(text: str, n: int = 6) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        q = re.sub(r"^[\s\-*•\d.)\]]+", "", ln).strip().strip('"“”')
        if 10 <= len(q) <= 140 and q.endswith("?") and q not in out:
            out.append(q)
    return out[:n]


async def suggest_questions(svc, deck_id: str, n: int = 6) -> list[str]:
    store = svc.store
    slides = store.slides(deck_id)
    if svc.llm.name == "fake":
        return heuristic(slides, n)
    material = "\n\n".join(f"Slide {s['n']} - {s['title']}\n{s.get('notes') or s.get('body', '')}" for s in slides)[:6000]
    msgs = [{"role": "user", "content": PROMPT.format(n=n, material=material)}]
    for model in svc.settings.script_models():
        try:
            text = await with_backoff(lambda m=model: svc.llm.complete(model=m, messages=msgs, max_tokens=600, temperature=0.7),
                                      attempts=3)
            qs = clean_questions(text, n)
            if len(qs) >= 3:
                return qs
        except Exception as e:
            log.warning("suggestions via %s failed: %s", model, e)
    return heuristic(slides, n)
