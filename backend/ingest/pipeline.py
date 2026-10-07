"""Ingest pipeline: parse -> render -> narrate -> index (-> audio, added in phase 2)."""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Awaitable, Callable

from ..services import Services
from .cursor import CursorMatcher, build_sentence_map
from .layout import extract_layout, placeholder_layout
from .narration import build_narration
from .parse import parse_pptx
from .render import render_slides

log = logging.getLogger("ingest.pipeline")

PostHook = Callable[[Services, str], Awaitable[None]]
POST_HOOKS: list[PostHook] = []  # phase 2 registers audio pre-generation here


def build_layout(store, deck_id: str, slides: list[dict]) -> dict:
    """Line boxes per slide: from the PDF when LibreOffice rendered it, else from the placeholder geometry."""
    try:
        if store.pdf_path(deck_id).exists():
            lay = extract_layout(store.pdf_path(deck_id))
            if len(lay) >= len(slides):
                return lay
    except Exception as e:
        log.warning("layout extraction failed (%s); using placeholder geometry", e)
    return placeholder_layout(slides)


async def run_ingest(svc: Services, deck_id: str) -> None:
    store, st = svc.store, svc.settings

    def stage(name: str, progress: float, **kw):
        store.update_meta(deck_id, stage=name, progress=round(progress, 3), **kw)

    try:
        stage("parsing", 0.02)
        parsed = await asyncio.to_thread(parse_pptx, store.source_path(deck_id))
        if not parsed:
            raise ValueError("presentation has no slides")
        slides = [p.as_dict() for p in parsed]
        store.write_slides(deck_id, slides)

        stage("rendering", 0.10)
        mode = await asyncio.to_thread(render_slides, store.source_path(deck_id), store.slides_dir(deck_id), slides,
                                       store.pdf_path(deck_id))
        store.update_meta(deck_id, render=mode, slide_count=len(slides))
        store.write_layout(deck_id, await asyncio.to_thread(build_layout, store, deck_id, slides))

        stage("narration", 0.25)

        def on_progress(done: int, total: int):
            store.update_meta(deck_id, progress=round(0.25 + 0.6 * done / total, 3), stage=f"narration {done}/{total}")

        # Sequential on purpose: Groq free tier is rate limited.
        narration = await build_narration(svc.llm, st, slides, progress=on_progress)
        store.write_narration(deck_id, narration)

        try:  # pointer targets are a nicety: never fail ingest over them
            matcher = CursorMatcher(store.layout(deck_id), svc.kb.embedder)
            store.write_cursor_map(deck_id, await asyncio.to_thread(build_sentence_map, matcher, narration))
        except Exception as e:
            log.warning("cursor map failed: %s", e)

        stage("indexing", 0.9)
        n = await asyncio.to_thread(svc.kb.index_slides, deck_id, slides, narration)
        store.update_meta(deck_id, status="ready", stage="ready", progress=1.0, chunks=n,
                          llm=svc.llm.name, embedder=svc.kb.embedder.name)
        log.info("deck %s ready (%d slides, %d chunks)", deck_id, len(slides), n)
    except Exception as e:
        log.error("ingest failed: %s\n%s", e, traceback.format_exc())
        store.update_meta(deck_id, status="error", stage="error", error=str(e))
        return

    for hook in POST_HOOKS:
        try:
            await hook(svc, deck_id)
        except Exception as e:  # audio pre-gen failing must never break a ready deck
            log.error("post-ingest hook %s failed: %s", getattr(hook, "__name__", hook), e)
