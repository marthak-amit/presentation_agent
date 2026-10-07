from __future__ import annotations

import asyncio

import httpx

from ..config import Settings


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._c = client or httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0))

    RETRY_DELAYS = (0.4, 1.2)  # transient 429 / 5xx / network blips are retried before we fall back to another voice

    async def synth(self, text: str, previous: str = "", next: str = "") -> bytes:
        last: Exception | None = None
        for attempt in range(len(self.RETRY_DELAYS) + 1):
            try:
                return await self._once(text, previous, next)
            except _Transient as e:
                last = e
                if attempt < len(self.RETRY_DELAYS):
                    await asyncio.sleep(max(self.RETRY_DELAYS[attempt], e.retry_after or 0))
        raise RuntimeError(str(last))

    async def _once(self, text: str, previous: str = "", next: str = "") -> bytes:
        s = self.s
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}/stream"
        body = {"text": text, "model_id": s.elevenlabs_model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}}
        if previous:
            body["previous_text"] = previous[-400:]  # neighbouring sentences keep intonation continuous across clips
        if next:
            body["next_text"] = next[:400]
        buf = bytearray()
        try:
            async with self._c.stream("POST", url, params={"output_format": "mp3_44100_128"}, json=body,
                                      headers={"xi-api-key": s.elevenlabs_api_key, "accept": "audio/mpeg"}) as r:
                if r.status_code != 200:
                    detail = (await r.aread())[:200]
                    msg = f"ElevenLabs HTTP {r.status_code}: {detail!r}"
                    if r.status_code == 429 or r.status_code >= 500:
                        ra = r.headers.get("retry-after")
                        raise _Transient(msg, float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
                    raise RuntimeError(msg)  # 401 / 422 ...: retrying cannot help
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise _Transient(f"ElevenLabs network error: {type(e).__name__}") from e
        if len(buf) < 200:
            raise _Transient("ElevenLabs returned empty audio")
        return bytes(buf)


class _Transient(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after
