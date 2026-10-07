from __future__ import annotations

import httpx

from ..config import Settings


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._c = client or httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0))

    async def synth(self, text: str) -> bytes:
        s = self.s
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}/stream"
        body = {"text": text, "model_id": s.elevenlabs_model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}}
        buf = bytearray()
        async with self._c.stream("POST", url, params={"output_format": "mp3_44100_128"}, json=body,
                                  headers={"xi-api-key": s.elevenlabs_api_key, "accept": "audio/mpeg"}) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:200]
                raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {detail!r}")
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
        if len(buf) < 200:
            raise RuntimeError("ElevenLabs returned empty audio")
        return bytes(buf)
