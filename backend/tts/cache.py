"""Audio file cache with a sidecar meta file. Real audio is never regenerated; mock audio is upgraded."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from .chain import FallbackTTS


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _meta_path(p: Path) -> Path:
    return p.with_suffix(".meta.json")


class AudioCache:
    def __init__(self, tts: FallbackTTS):
        self.tts = tts
        self._locks: dict[str, asyncio.Lock] = {}  # one generation per file at a time (warm-up, session and pre-gen overlap)

    def _fresh(self, path: Path, text: str) -> bool:
        if not path.exists() or path.stat().st_size < 100:
            return False
        try:
            meta = json.loads(_meta_path(path).read_text())
        except (OSError, json.JSONDecodeError):
            return True  # hand-placed file without meta: trust it
        if meta.get("sha") != _sha(text):
            return False  # text changed
        if meta.get("provider") == "fake" and self.tts.real:
            return False  # upgrade mock audio once a real provider exists
        provider = meta.get("provider", "")
        primary = self.tts.primary_real
        if primary and provider != primary and not self.tts.is_down(primary):
            return False  # made by a fallback voice (e.g. Aura while ElevenLabs was failing): upgrade to the preferred voice
        if provider != "fake" and meta.get("voice", None) != self.tts.voice_of(provider):
            return False  # ELEVENLABS_VOICE_ID / model / Aura voice changed -> re-voice
        return True

    async def ensure(self, path: Path, text: str, previous: str = "", next: str = "") -> tuple[Path, str]:
        """Return (path, provider). Generates only when missing/stale. previous/next only shape the voice's prosody."""
        lock = self._locks.setdefault(str(path), asyncio.Lock())
        async with lock:
            if self._fresh(path, text):
                try:
                    provider = json.loads(_meta_path(path).read_text()).get("provider", "cached")
                except (OSError, json.JSONDecodeError):
                    provider = "cached"
                return path, provider
            audio, provider = await self.tts.synth(text, previous, next)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{id(audio)}.tmp")
            tmp.write_bytes(audio)
            tmp.replace(path)
            _meta_path(path).write_text(json.dumps({"sha": _sha(text), "provider": provider,
                                                    "voice": self.tts.voice_of(provider)}))
            return path, provider
