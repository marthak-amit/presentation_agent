"""Mock TTS: valid *silent* MP3 whose length tracks the text, so timing/UX can be tested without keys."""
from __future__ import annotations

FRAME = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)  # MPEG1 L3, 128kbps, 44.1kHz, stereo = 417 bytes, 26.12ms
FRAME_S = 1152 / 44100


def silent_mp3(seconds: float) -> bytes:
    return FRAME * max(1, int(seconds / FRAME_S))


def estimate_seconds(text: str, wpm: float = 165.0) -> float:
    words = max(1, len(text.split()))
    return max(0.7, words * 60.0 / wpm)


class FakeTTS:
    name = "fake"

    def __init__(self, speed: float = 1.0):
        self.speed = speed  # >1 shortens the silent clips (tests)

    async def synth(self, text: str, previous: str = "", next: str = "") -> bytes:
        return silent_mp3(estimate_seconds(text) / self.speed)
