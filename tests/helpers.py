from __future__ import annotations

import asyncio
import shutil
import time

from backend.ingest.pipeline import run_ingest
from backend.session import messages as M
from backend.session.session import PresenterSession
from backend.tts.pregen import pregenerate_deck_audio

from .conftest import SAMPLE


async def make_ready_deck(svc, audio: bool = True) -> str:
    deck_id = svc.store.create("sample")
    shutil.copy(SAMPLE, svc.store.source_path(deck_id))
    await run_ingest(svc, deck_id)
    assert svc.store.meta(deck_id)["status"] == "ready"
    if audio:
        await pregenerate_deck_audio(svc, deck_id)
    return deck_id


class FakeClient:
    """Plays the role of the browser: records messages and acks playback after `play_s` seconds."""

    def __init__(self, svc, play_s: float = 0.01, auto_ack: bool = True):
        self.svc = svc
        self.messages: list[dict] = []
        self.play_s = play_s
        self.auto_ack = auto_ack
        self.session = PresenterSession(svc, self._recv)
        self._acks: dict[str, asyncio.Task] = {}
        self.fade_times: list[float] = []

    async def _recv(self, obj: dict) -> None:
        obj = dict(obj, _t=time.monotonic())
        self.messages.append(obj)
        t = obj["type"]
        if t in ("play_sentence", "play_clip", "play_answer") and self.auto_ack:
            pid = obj["play_id"]
            self._acks[pid] = asyncio.create_task(self._ack(pid))
        elif t == "pause":
            for task in self._acks.values():
                task.cancel()
            self._acks.clear()

    async def _ack(self, pid: str) -> None:
        await asyncio.sleep(self.play_s)
        self._acks.pop(pid, None)
        await self.session.handle(M.AudioEnded(play_id=pid))

    async def ack_now(self, pid: str) -> None:
        await self.session.handle(M.AudioEnded(play_id=pid))

    # helpers
    async def sim(self, text: str, final: bool = True, end: bool = False, conf: float = 0.95):
        await self.session.handle(M.SimTranscript(text=text, is_final=final, confidence=conf, utterance_end=end))

    async def say(self, text: str):
        """A complete utterance: interim -> final -> UtteranceEnd."""
        await self.sim(text, final=False)
        await self.sim(text, final=True, end=True)

    def idx(self, pred) -> int:
        for i, m in enumerate(self.messages):
            if pred(m):
                return i
        return -1

    async def open(self, deck_id: str, barge_in: bool = True):
        await self.session.open(deck_id, barge_in)

    async def ctl(self, action: str, **kw):
        await self.session.handle(M.Control(action=action, **kw))

    def of(self, *types: str) -> list[dict]:
        return [m for m in self.messages if m["type"] in types]

    def states(self) -> list[str]:
        return [m["state"] for m in self.of("state")]

    async def wait_for(self, pred, timeout: float = 15.0, desc: str = ""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = pred()
            if r:
                return r
            await asyncio.sleep(0.005)
        raise AssertionError(f"timeout waiting for {desc or pred}; last messages: {[m['type'] for m in self.messages[-8:]]}")

    async def wait_state(self, state: str, timeout: float = 15.0):
        return await self.wait_for(lambda: self.session.state.value == state, timeout, f"state {state}")

    async def close(self):
        await self.session.close()
