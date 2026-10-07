"""Pre-generate per-sentence audio for a deck: data/decks/{id}/audio/{n}/s{i}.mp3 (cached)."""
from __future__ import annotations

import asyncio
import logging

from ..services import Services
from .clips import ensure_all_clips

log = logging.getLogger("tts.pregen")
CONCURRENCY = 2  # ElevenLabs free tier allows ~2-3 parallel requests


async def pregenerate_deck_audio(svc: Services, deck_id: str) -> None:
    store, cache = svc.store, svc.audio
    narration = store.narration(deck_id)
    jobs = [(s["n"], i, text) for s in narration for i, text in enumerate(s["sentences"])]
    store.update_meta(deck_id, audio={"done": 0, "total": len(jobs)})
    await ensure_all_clips(cache, svc.settings.stock_dir, svc.settings.presenter_name)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    fake = 0

    async def one(n: int, i: int, text: str):
        nonlocal done, fake
        async with sem:
            try:
                _, provider = await cache.ensure(store.audio_path(deck_id, n, i), text)
                if provider == "fake" and svc.tts.real:
                    fake += 1  # a real voice is configured but this sentence fell back to silent mock audio
            except Exception as e:  # never fail the whole deck on one sentence
                log.warning("audio %s/%s failed: %s", n, i, e)
            done += 1
            if done % 3 == 0 or done == len(jobs):
                store.update_meta(deck_id, audio={"done": done, "total": len(jobs)})

    await asyncio.gather(*(one(*j) for j in jobs))
    store.update_meta(deck_id, audio={"done": done, "total": len(jobs), "fake": fake}, audio_provider=svc.tts.primary_name)
    if fake:
        log.warning("deck %s: %d sentence(s) have silent mock audio (voice provider failed); POST /decks/%s/audio retries", deck_id, fake, deck_id)
    log.info("deck %s: %d audio files ready", deck_id, done)
