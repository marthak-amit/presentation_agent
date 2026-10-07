"""Debug hub: fan-out of every session's outgoing events to /ws/debug subscribers, plus a session registry."""
from __future__ import annotations

import asyncio
import time
from collections import deque


class DebugHub:
    def __init__(self, keep: int = 200):
        self.buffer: deque[dict] = deque(maxlen=keep)
        self.subs: set[asyncio.Queue] = set()
        self.sessions: dict[str, object] = {}

    def publish(self, session_id: str, event: dict) -> None:
        item = {"session_id": session_id, "ts": round(time.time(), 3), "event": event}
        self.buffer.append(item)
        for q in list(self.subs):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass  # slow debug client: drop rather than block the talk

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    def register(self, session) -> None:
        self.sessions[session.session_id] = session

    def unregister(self, session) -> None:
        self.sessions.pop(session.session_id, None)
