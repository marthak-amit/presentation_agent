"""Provider chain with a circuit breaker: ElevenLabs -> Aura -> fake. Never raises."""
from __future__ import annotations

import logging
import os
import time

from ..config import Settings
from .aura import AuraTTS
from .base import TTSProvider
from .elevenlabs import ElevenLabsTTS
from .fake import FakeTTS

log = logging.getLogger("tts")


class FallbackTTS:
    def __init__(self, providers: list[TTSProvider], cooldown_s: float = 45.0, clock=time.monotonic):
        assert providers, "need at least one provider"
        self.providers = providers
        self.cooldown_s = cooldown_s
        self._down_until: dict[str, float] = {}
        self._clock = clock
        self.last_error: str | None = None
        self.last_provider: str = ""  # who served the most recent request

    @property
    def real(self) -> bool:
        return any(p.name != "fake" for p in self.providers)

    def is_down(self, name: str) -> bool:
        return self._down_until.get(name, 0) > self._clock()

    @property
    def primary_real(self) -> str:
        """Name of the preferred (first) real provider, or '' when only the mock exists."""
        return self.providers[0].name if self.providers and self.providers[0].name != "fake" else ""

    def voice_of(self, provider_name: str) -> str:
        """Identity of the voice a provider currently produces (voice id + model); stored next to every cached clip."""
        for p in self.providers:
            if p.name == provider_name:
                return getattr(p, "voice_key", "")
        return ""

    def degraded(self) -> bool:
        """True while any real provider is in its cool-down window."""
        return any(self._down_until.get(p.name, 0) > self._clock() for p in self.providers if p.name != "fake")

    @property
    def primary_name(self) -> str:
        return self.providers[0].name

    async def synth(self, text: str, previous: str = "", next: str = "") -> tuple[bytes, str]:
        for p in self.providers:
            if self._down_until.get(p.name, 0) > self._clock():
                continue
            try:
                audio = await p.synth(text, previous, next)
                self.last_provider = p.name
                if p.name == self.primary_real:
                    self.last_error = None
                return audio, p.name
            except Exception as e:
                self.last_error = f"{p.name}: {e}"
                if p.name != "fake":
                    self._down_until[p.name] = self._clock() + self.cooldown_s
                    log.warning("TTS provider %s failed (%s); disabled for %.0fs", p.name, e, self.cooldown_s)
        self.last_provider = "fake"
        return await FakeTTS().synth(text), "fake"


def make_tts(settings: Settings) -> FallbackTTS:
    chain: list[TTSProvider] = []
    if settings.use_elevenlabs:
        chain.append(ElevenLabsTTS(settings))
    if settings.use_aura:
        chain.append(AuraTTS(settings))
    if not chain:
        log.warning("no TTS keys -> FakeTTS (silent audio, mock)")
    chain.append(FakeTTS(speed=float(os.getenv("MOCK_TTS_SPEED", "1") or 1)))
    return FallbackTTS(chain)
