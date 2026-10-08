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
    if not store.exists(deck_id):
        return
    narration = store.narration(deck_id)
    flat = [(s["n"], i, text) for s in narration for i, text in enumerate(s["sentences"])]
    jobs = [(n, i, text, flat[k - 1][2] if k else "", flat[k + 1][2] if k + 1 < len(flat) else "")
            for k, (n, i, text) in enumerate(flat)]
    store.update_meta(deck_id, audio={"done": 0, "total": len(jobs)})
    await ensure_all_clips(cache, svc.settings.stock_dir, svc.settings.presenter_name)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    fake = 0
    other = 0
    by_provider: dict[str, int] = {}

    async def one(n: int, i: int, text: str, prev: str, nxt: str):
        nonlocal done, fake, other
        async with sem:
            if not store.alive(deck_id):
                return  # the deck was deleted while we were generating: stop spending credits on it
            try:
                _, provider = await cache.ensure(store.audio_path(deck_id, n, i), text, prev, nxt)
                by_provider[provider] = by_provider.get(provider, 0) + 1
                if provider == "fake" and svc.tts.real:
                    fake += 1  # a real voice is configured but this sentence fell back to silent mock audio
                elif svc.tts.primary_real and provider != svc.tts.primary_real:
                    other += 1  # a fallback voice (Aura) made this sentence - not the configured voice
            except Exception as e:  # never fail the whole deck on one sentence
                log.warning("audio %s/%s failed: %s", n, i, e)
            done += 1
            if done % 3 == 0 or done == len(jobs):
                store.update_meta(deck_id, audio={"done": done, "total": len(jobs)})

    await asyncio.gather(*(one(*j) for j in jobs))
    store.update_meta(deck_id, audio={"done": done, "total": len(jobs), "fake": fake, "other_voice": other,
                                      "providers": by_provider}, audio_provider=svc.tts.primary_name)
    if fake:
        log.warning("deck %s: %d sentence(s) have silent mock audio (voice provider failed); POST /decks/%s/audio retries", deck_id, fake, deck_id)
    log.info("deck %s: %d audio files ready", deck_id, done)
