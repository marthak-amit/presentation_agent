from __future__ import annotations

import httpx

from ..config import Settings


class AuraTTS:
    """Deepgram Aura TTS (fallback when ELEVENLABS_API_KEY is empty)."""

    name = "aura"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._c = client or httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0))

    @property
    def voice_key(self) -> str:
        return self.s.deepgram_tts_model

    async def synth(self, text: str, previous: str = "", next: str = "") -> bytes:
        r = await self._c.post(
            "https://api.deepgram.com/v1/speak",
            params={"model": self.s.deepgram_tts_model, "encoding": "mp3"},
            headers={"Authorization": f"Token {self.s.deepgram_api_key}", "Content-Type": "application/json"},
            json={"text": text},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Aura HTTP {r.status_code}: {r.text[:200]}")
        if len(r.content) < 200:
            raise RuntimeError("Aura returned empty audio")
        return r.content
