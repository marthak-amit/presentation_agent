from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol


@dataclass
class STTEvent:
    kind: Literal["transcript", "utterance_end", "speech_started"]
    text: str = ""
    is_final: bool = False
    confidence: float = 1.0


OnSTTEvent = Callable[[STTEvent], Awaitable[None]]


class STTStream(Protocol):
    name: str
    status: str  # up | down | mock

    async def start(self) -> None: ...
    async def send_audio(self, data: bytes) -> None: ...
    async def finalize(self) -> None: ...
    async def close(self) -> None: ...
