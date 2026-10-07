from __future__ import annotations

from typing import Protocol


class TTSProvider(Protocol):
    name: str

    async def synth(self, text: str, previous: str = "", next: str = "") -> bytes:
        """Return MP3 bytes for `text`. `previous`/`next` are the neighbouring sentences (prosody hints). Raise on failure."""
        ...
