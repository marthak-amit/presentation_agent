"""Mock STT: ignores audio. Transcripts are injected via the `sim_transcript` WS message."""
from __future__ import annotations

from .base import OnSTTEvent


class FakeSTT:
    name = "fake"
    status = "mock"

    def __init__(self, on_event: OnSTTEvent | None = None):
        self.on_event = on_event
        self.bytes_received = 0

    async def start(self) -> None:
        pass

    async def send_audio(self, data: bytes) -> None:
        self.bytes_received += len(data)

    async def finalize(self) -> None:
        pass

    async def close(self) -> None:
        pass
