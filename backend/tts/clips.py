"""Stock clips (pre-generated, served at /stock/{key}.mp3)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .cache import AudioCache

log = logging.getLogger("tts.clips")

# key -> text template ({name} = PRESENTER_NAME)
STOCK_CLIPS: dict[str, str] = {
    "go_ahead": "Sure, go ahead.",
    "anything_else": "Does that answer your question? Anything else?",
    "continue": "Great, let's continue.",
    "filler": "Good question — give me a second.",
    "followup": "Great question — I'll have {name} follow up on that.",
    "open_qa": "That's everything I had. What questions do you have?",
}


def clip_text(key: str, presenter: str) -> str:
    return STOCK_CLIPS[key].format(name=presenter)


def clip_path(stock_dir: Path, key: str) -> Path:
    return stock_dir / f"{key}.mp3"


def clip_url(key: str) -> str:
    return f"/stock/{key}.mp3"


async def ensure_clip(cache: AudioCache, stock_dir: Path, key: str, presenter: str) -> str:
    await cache.ensure(clip_path(stock_dir, key), clip_text(key, presenter))
    return clip_url(key)


async def ensure_all_clips(cache: AudioCache, stock_dir: Path, presenter: str) -> int:
    await asyncio.gather(*(ensure_clip(cache, stock_dir, k, presenter) for k in STOCK_CLIPS))
    return len(STOCK_CLIPS)
