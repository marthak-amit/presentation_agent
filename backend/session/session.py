"""PresenterSession: one per WebSocket. Owns playhead, state machine, audio sequencing."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

from ..services import Services
from ..stt.base import STTEvent
from ..stt.fake import FakeSTT
from ..tts.clips import clip_text, clip_url, ensure_clip
from . import messages as M
from .plan import DeckPlan
from .playback import PlaybackTracker
from .state import InvalidTransition, S, StateMachine

log = logging.getLogger("session")

SendFn = Callable[[dict], Awaitable[None]]


def audio_timeout(num_bytes: int) -> float:
    """Upper bound on how long a clip can legitimately play (assumes >= 48 kbps)."""
    return num_bytes / 6000.0 + 4.0


class PresenterSession:
    def __init__(self, svc: Services, send: SendFn, session_id: str | None = None):
        self.svc = svc
        self.cfg = svc.settings
        self._send_raw = send
        self.session_id = session_id or uuid.uuid4().hex[:10]
        self.sm = StateMachine()
        self.tracker = PlaybackTracker()
        self.plan: DeckPlan | None = None
        self.deck_id = ""
        self.playhead: tuple[int, int] = (1, 0)
        self.barge_in_enabled = True
        self.closed = False
        self.stt = FakeSTT()
        self._present_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.resume_ts = 0.0
        self.pause_reason = ""

    # ------------------------------------------------------------------ plumbing
    @property
    def state(self) -> S:
        return self.sm.state

    async def _send(self, msg: M._Msg | dict) -> None:
        if self.closed:
            return
        try:
            await self._send_raw(msg.wire() if isinstance(msg, M._Msg) else msg)
        except Exception as e:  # socket gone
            log.debug("send failed: %s", e)
            self.closed = True

    def _spawn(self, coro, name: str = "") -> asyncio.Task:
        t = asyncio.create_task(coro, name=name or None)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    async def _emit_state(self, reason: str = "") -> None:
        await self._send(M.StateMsg(state=self.sm.state.value, reason=reason, slide_n=self.playhead[0],
                                    sentence_i=self.playhead[1], barge_in=self.barge_in_enabled,
                                    stt=self.stt.status))

    async def _go(self, to: S, reason: str = "") -> bool:
        changed = self.sm.go(to, reason)
        if changed:
            if to == S.PRESENTING:
                self.resume_ts = time.monotonic()
            await self._emit_state(reason)
        return changed

    # ------------------------------------------------------------------ lifecycle
    async def open(self, deck_id: str, barge_in: bool = True, slide_n: int | None = None, sentence_i: int = 0) -> None:
        store = self.svc.store
        if not store.exists(deck_id) or store.meta(deck_id).get("status") != "ready":
            await self._send(M.ErrorMsg(message=f"deck {deck_id} not found or not ready"))
            return
        self.deck_id = deck_id
        self.plan = DeckPlan.load(store, deck_id)
        if not len(self.plan):
            await self._send(M.ErrorMsg(message="deck has no slides"))
            return
        self.playhead = (self.plan.first, 0)
        if slide_n is not None and self.plan.has(slide_n):
            self.playhead = (slide_n, max(0, sentence_i))
        self.barge_in_enabled = barge_in
        await self._setup_stt()
        # Warm the stock clips so the first interruption never waits on TTS.
        self._spawn(self._warm_clips(), "warm-clips")
        await self._send(M.SessionReady(
            session_id=self.session_id, deck_id=deck_id, slide_count=len(self.plan),
            presenter=self.cfg.presenter_name, barge_in=barge_in,
            services={"llm": self.svc.llm.name, "stt": self.stt.name, "tts": self.svc.tts.primary_name},
        ))
        await self._send(M.SlideMsg(slide_n=self.playhead[0]))
        await self._emit_state("ready")

    async def _setup_stt(self) -> None:  # replaced in phase 3
        self.stt = FakeSTT(self._on_stt_event)
        await self.stt.start()

    async def _warm_clips(self) -> None:
        from ..tts.clips import ensure_all_clips

        try:
            await ensure_all_clips(self.svc.audio, self.cfg.stock_dir, self.cfg.presenter_name)
        except Exception as e:
            log.warning("clip warmup failed: %s", e)

    async def close(self) -> None:
        self.closed = True
        await self._cancel_present()
        self.tracker.cancel_all()
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        await self.stt.close()

    # ------------------------------------------------------------------ inbound
    async def handle(self, msg: M.ClientMessage) -> None:
        if isinstance(msg, M.Control):
            await self._on_control(msg)
        elif isinstance(msg, M.AudioEnded):
            self.tracker.ended(msg.play_id, msg.interrupted)
        elif isinstance(msg, M.Ping):
            await self._send(M.Pong())
        elif isinstance(msg, M.SetBargeIn):
            self.barge_in_enabled = msg.enabled
            await self._emit_state("barge_in_toggle")
        elif isinstance(msg, M.HandRaise):
            await self._on_hand_raise()
        elif isinstance(msg, M.Ptt):
            await self._on_ptt(msg.active)
        elif isinstance(msg, M.SimTranscript):
            await self._on_sim(msg)

    async def on_audio(self, data: bytes) -> None:
        await self.stt.send_audio(data)

    # ------------------------------------------------------------------ controls
    async def _on_control(self, c: M.Control) -> None:
        if self.plan is None:
            return
        a = c.action
        if a == "start":
            if self.state in (S.IDLE, S.END):
                if self.state == S.END:
                    self.playhead = (self.plan.first, 0)
                await self._begin_presenting("start")
            elif self.state == S.PAUSED:
                await self._resume_from_pause("start")
        elif a == "restart":
            await self._restart()
        elif a == "pause":
            if self.state == S.PRESENTING:
                await self._pause("user")
        elif a == "resume":
            await self._resume_from_pause("user")
        elif a in ("next", "prev", "goto"):
            n = self.playhead[0]
            if a == "next":
                n = self.plan.next_slide(n)
            elif a == "prev":
                n = self.plan.prev_slide(n)
            elif c.slide_n is not None and self.plan.has(c.slide_n):
                n = c.slide_n
            await self._jump(n)

    async def _begin_presenting(self, reason: str) -> None:
        await self._go(S.PRESENTING, reason)
        await self._send(M.SlideMsg(slide_n=self.playhead[0]))
        self._present_task = asyncio.create_task(self._present_loop(), name="present-loop")

    async def _cancel_present(self) -> None:
        t, self._present_task = self._present_task, None
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

    async def _pause(self, reason: str, fade_ms: int = 150) -> None:
        """Stop the present loop, fade out client audio, state=PAUSED. Playhead stays on the current sentence."""
        await self._cancel_present()
        self.tracker.cancel_all()
        self.pause_reason = reason
        await self._send(M.Pause(fade_ms=fade_ms, reason=reason))
        await self._go(S.PAUSED, reason)

    async def _resume_from_pause(self, reason: str) -> None:
        if self.state == S.PAUSED:
            await self._begin_presenting(reason)

    async def _jump(self, n: int) -> None:
        assert self.plan is not None
        if self.state in (S.ANSWERING, S.LISTENING):
            return
        was_presenting = self.state == S.PRESENTING
        if was_presenting:
            await self._cancel_present()
            self.tracker.cancel_all()
            await self._send(M.Pause(fade_ms=100, reason="jump"))
        self.playhead = (n, 0)
        await self._send(M.SlideMsg(slide_n=n))
        if was_presenting:
            self._present_task = asyncio.create_task(self._present_loop(), name="present-loop")
        else:
            await self._emit_state("jump")

    async def _restart(self) -> None:
        assert self.plan is not None
        await self._cancel_present()
        self.tracker.cancel_all()
        await self._on_restart_cleanup()
        await self._send(M.Pause(fade_ms=100, reason="restart"))
        self.playhead = (self.plan.first, 0)
        if self.state != S.IDLE:
            if self.sm.can(S.IDLE):
                await self._go(S.IDLE, "restart")
        await self._begin_presenting("restart")

    async def _on_restart_cleanup(self) -> None:  # phase 3 hook
        pass

    # ------------------------------------------------------------------ presenting
    async def _present_loop(self) -> None:
        assert self.plan is not None
        plan = self.plan
        try:
            while self.sm.state == S.PRESENTING and not self.closed:
                n, i = self.playhead
                text = plan.sentence(n, i)
                try:
                    if text is None:  # slide without narration: show it briefly
                        await self._send(M.SlideMsg(slide_n=n))
                        await asyncio.sleep(2.0)
                    else:
                        await self._play_sentence(n, i, text)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("sentence %s/%s failed; skipping", n, i)
                    await asyncio.sleep(0.3)
                if self.sm.state != S.PRESENTING:
                    return
                nxt = plan.next_pos(n, i)
                if nxt is None:
                    await self._finish_deck()
                    return
                if nxt[0] != n:
                    await asyncio.sleep(0.3)  # natural beat between slides
                self.playhead = nxt
        except asyncio.CancelledError:
            raise

    def _sentence_url(self, n: int, i: int) -> str:
        return f"/media/{self.deck_id}/audio/{n}/s{i}.mp3"

    async def _play_sentence(self, n: int, i: int, text: str) -> None:
        assert self.plan is not None
        store, cache = self.svc.store, self.svc.audio
        path, _provider = await cache.ensure(store.audio_path(self.deck_id, n, i), text)
        nxt = self.plan.next_pos(n, i)
        next_url = None
        if nxt is not None:
            nt = self.plan.sentence(*nxt)
            if nt is not None:
                self._spawn(cache.ensure(store.audio_path(self.deck_id, *nxt), nt), "prefetch")  # warm cache
                next_url = self._sentence_url(*nxt)
        play_id = self.tracker.new_id("s")
        fut = self.tracker.expect(play_id)
        self._on_speaking(text)
        await self._send(M.PlaySentence(play_id=play_id, slide_n=n, sentence_i=i, text=text,
                                        url=self._sentence_url(n, i), next_url=next_url))
        await self.tracker.wait(play_id, fut, audio_timeout(path.stat().st_size))

    def _on_speaking(self, text: str) -> None:  # phase 3 hook (echo guard)
        pass

    async def _finish_deck(self) -> None:
        await self._go(S.END, "deck_finished")
        await self._send(M.Summary(questions=[], unanswered=[]))
        await self._enter_open_qa()

    async def _enter_open_qa(self) -> None:
        """Say the closing line, then listen for questions (phase 3 handles the questions)."""
        url = await ensure_clip(self.svc.audio, self.cfg.stock_dir, "open_qa", self.cfg.presenter_name)
        play_id = self.tracker.new_id("c")
        fut = self.tracker.expect(play_id)
        await self._send(M.PlayClip(play_id=play_id, clip="open_qa",
                                    text=clip_text("open_qa", self.cfg.presenter_name), url=url))
        await self.tracker.wait(play_id, fut, 8.0)
        await self._go(S.OPEN_QA, "open_qa")

    # ------------------------------------------------------------------ phase 3 hooks
    async def _on_stt_event(self, ev: STTEvent) -> None:
        pass

    async def _on_hand_raise(self) -> None:
        pass

    async def _on_ptt(self, active: bool) -> None:
        pass

    async def _on_sim(self, m: M.SimTranscript) -> None:
        pass
