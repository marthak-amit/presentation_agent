"""Tracks audio items the client was told to play, so the server can await 'audio_ended'."""
from __future__ import annotations

import asyncio
import itertools


class PlaybackTracker:
    def __init__(self):
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._counter = itertools.count(1)
        self.last_started_text = ""

    def new_id(self, prefix: str = "p") -> str:
        return f"{prefix}{next(self._counter)}"

    def expect(self, play_id: str) -> asyncio.Future[bool]:
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[play_id] = fut
        return fut

    def ended(self, play_id: str, interrupted: bool = False) -> bool:
        fut = self._pending.pop(play_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(not interrupted)
        return True

    def cancel_all(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(False)
        self._pending.clear()

    async def wait(self, play_id: str, fut: asyncio.Future[bool], timeout: float) -> bool:
        """True if played to the end; False if interrupted or timed out (client never acked)."""
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            self._pending.pop(play_id, None)
            return False

    @property
    def pending(self) -> int:
        return len(self._pending)
