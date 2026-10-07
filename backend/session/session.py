"""PresenterSession: one per WebSocket. Owns playhead, state machine, audio sequencing, barge-in and live Q&A."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..ingest.cursor import CursorMatcher, build_sentence_map
from ..llm.prompts import qa_messages
from ..llm.qa import Failed, Filler, Finished, ModelSwitch, QAEngine, Text, ToolUsed
from ..rag.store import format_context
from ..services import Services
from ..stt.base import STTEvent
from ..stt.deepgram import DeepgramSTT
from ..stt.fake import FakeSTT
from ..tts.clips import clip_text, ensure_clip
from . import messages as M
from .barge_in import (ALL_TRIGGERS, BargeDecision, BargeInDetector, EchoGuard, UtteranceBuffer, is_dismissal,
                       strip_leading_trigger)
from .plan import DeckPlan
from .playback import PlaybackTracker
from .sentences_stream import SentenceStreamer
from .state import S, StateMachine

log = logging.getLogger("session")

SendFn = Callable[[dict], Awaitable[None]]
FOLLOWUP_RE = re.compile(r"follow[\s-]?up", re.I)


def audio_timeout(num_bytes: int) -> float:
    """Upper bound on how long a clip can legitimately play (assumes >= 48 kbps)."""
    return num_bytes / 6000.0 + 4.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SessionToolbox:
    """Tools the Q&A model may call."""

    def __init__(self, session: "PresenterSession"):
        self.s = session

    async def run(self, name: str, args: dict) -> str:
        s = self.s
        if name == "search_kb":
            query = str(args.get("query", ""))
            chunks = await asyncio.to_thread(s.svc.kb.search, s.deck_id, query, 3)
            return format_context(chunks, 1000) or "No relevant results."
        if name == "goto_slide":
            try:
                n = int(args.get("n"))
            except (TypeError, ValueError):
                return "Invalid slide number."
            if s.plan is None or not s.plan.has(n):
                return f"There is no slide {n}."
            s._answer_slide = n
            await s._send(M.SlideMsg(slide_n=n, temporary=True))
            return f"Now showing slide {n}: {s.plan.title(n)}"
        if name == "resume_presenting":
            s._resume_requested = True
            return "OK. You will go straight back to presenting after this answer."
        return f"Unknown tool {name}."


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
        self.pause_reason = ""
        self._present_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        # Q&A
        self.echo = EchoGuard(self.cfg.echo_threshold)
        self.detector = BargeInDetector(threshold=self.cfg.barge_fuzzy_threshold, min_confidence=self.cfg.barge_min_confidence,
                                        cooldown_s=self.cfg.barge_cooldown_s, inline_words=self.cfg.question_inline_words,
                                        echo=self.echo)
        self.buf = UtteranceBuffer()
        self._qa_q: asyncio.Queue = asyncio.Queue()
        self._qa_task: asyncio.Task | None = None
        self._history: list[tuple[str, str]] = []
        self._resume_requested = False
        self._ptt_active = False
        self.interruptions: list[dict] = []
        self.unanswered: list[dict] = []
        self.last_trigger = ""
        self.last_model_info: dict = {}
        self._matcher: CursorMatcher | None = None
        self._cursor_map: dict = {}
        self._answer_slide: int | None = None
        self._last_stt_status = "unknown"

    # ------------------------------------------------------------------ plumbing
    @property
    def state(self) -> S:
        return self.sm.state

    async def _send(self, msg: M._Msg | dict) -> None:
        if self.closed:
            return
        wire = msg.wire() if isinstance(msg, M._Msg) else msg
        self._observe(wire)
        try:
            await self._send_raw(wire)
        except Exception as e:  # socket gone
            log.debug("send failed: %s", e)
            self.closed = True

    def _observe(self, wire: dict) -> None:
        """Mirror outgoing messages to the debug hub (audio payloads stripped)."""
        hub = self.svc.hub
        if wire.get("type") == "play_answer":
            wire = {**wire, "audio_b64": f"<{len(wire.get('audio_b64', ''))} b64 chars>"}
        hub.publish(self.session_id, wire)

    def _debug(self, event: dict) -> None:
        """Debug-only event (not sent to the presenter client)."""
        self.svc.hub.publish(self.session_id, event)

    def _spawn(self, coro, name: str = "") -> asyncio.Task:
        t = asyncio.create_task(coro, name=name or None)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def _warnings(self) -> list[str]:
        w: list[str] = []
        if self.stt.status == "down":
            w.append("Speech recognition offline - voice commands (\"Okay Agent\") won't work; use Hold to talk")
        tts = self.svc.tts
        if getattr(tts, "real", False) and getattr(tts, "last_error", None) and tts.degraded():
            w.append(f"TTS degraded ({tts.last_error[:80]}) - playing cached audio")
        return w

    async def _emit_state(self, reason: str = "") -> None:
        self._last_stt_status = self.stt.status
        await self._send(M.StateMsg(state=self.sm.state.value, reason=reason, slide_n=self.playhead[0],
                                    sentence_i=self.playhead[1], barge_in=self.barge_in_enabled,
                                    stt=self.stt.status, warnings=self._warnings()))

    async def _status_watch(self) -> None:
        """Re-emit state when the STT connection flips up/down (offline safety banner)."""
        while not self.closed:
            await asyncio.sleep(1.0)
            if self.stt.status != self._last_stt_status:
                await self._emit_state("stt_" + self.stt.status)

    async def _go(self, to: S, reason: str = "") -> bool:
        changed = self.sm.go(to, reason)
        if changed:
            if to == S.PRESENTING:
                self.detector.note_resume()
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
        self._cursor_map = store.cursor_map(deck_id)
        if not self._cursor_map and store.layout(deck_id):  # deck ingested before the pointer feature
            self._spawn(self._build_cursor_map(), "cursor-map")
        await self._setup_stt()
        # Warm the stock clips so the first interruption never waits on TTS.
        self._spawn(self._warm_clips(), "warm-clips")
        self._spawn(self._status_watch(), "stt-watch")
        await self._send(M.SessionReady(
            session_id=self.session_id, deck_id=deck_id, slide_count=len(self.plan),
            presenter=self.cfg.presenter_name, barge_in=barge_in,
            services={"llm": self.svc.llm.name, "stt": self.stt.name, "tts": self.svc.tts.primary_name},
        ))
        await self._send(M.SlideMsg(slide_n=self.playhead[0]))
        await self._emit_state("ready")

    async def _setup_stt(self) -> None:
        if self.cfg.use_real_stt:
            self.stt = DeepgramSTT(self.cfg, self._on_stt_event, keyterms=ALL_TRIGGERS)
        else:
            self.stt = FakeSTT(self._on_stt_event)
        await self.stt.start()

    def _get_matcher(self) -> CursorMatcher | None:
        if self._matcher is None:
            layout = self.svc.store.layout(self.deck_id)
            if not layout:
                return None
            try:
                emb = self.svc.kb.embedder
            except Exception:
                emb = None
            self._matcher = CursorMatcher(layout, emb)
        return self._matcher

    async def _build_cursor_map(self) -> None:
        def work():
            m = self._get_matcher()
            if m is None:
                return {}
            cmap = build_sentence_map(m, self.svc.store.narration(self.deck_id))
            self.svc.store.write_cursor_map(self.deck_id, cmap)
            return cmap

        try:
            self._cursor_map = await asyncio.to_thread(work)
        except Exception as e:
            log.warning("cursor map failed: %s", e)

    @staticmethod
    def _pointer(box: dict | None) -> M.Pointer | None:
        if not box:
            return None
        lines = [M.PointerLine(x0=ln["x0"], y0=ln["y0"], x1=ln["x1"], y1=ln["y1"],
                               words=[M.PointerWord(**w) for w in ln.get("words", [])]) for ln in box.get("lines", [])]
        return M.Pointer(x0=box["x0"], y0=box["y0"], x1=box["x1"], y1=box["y1"], text=box.get("text", ""), lines=lines)

    def _match_pointer(self, slide_n: int, text: str, others: tuple[int, ...] = ()) -> tuple[int, M.Pointer | None]:
        """Line of the slide being shown that `text` is about. If the sentence is clearly about a line on one of the
        other slides the answer was drawn from, point there instead (and show that slide)."""
        m = self._get_matcher()
        if m is None:
            return slide_n, None
        try:
            here = m.match(slide_n, text)
            best_slide, best = slide_n, here
            for o in others:
                if o == slide_n:
                    continue
                hit = m.match(o, text)
                if hit and hit["score"] >= 0.5 and hit["score"] > (best["score"] + 0.15 if best else 0):
                    best_slide, best = o, hit
            return best_slide, self._pointer(best)
        except Exception as e:  # the pointer must never break speech
            log.debug("pointer match failed: %s", e)
            return slide_n, None

    async def _warm_clips(self) -> None:
        from ..tts.clips import ensure_all_clips

        try:
            await ensure_all_clips(self.svc.audio, self.cfg.stock_dir, self.cfg.presenter_name)
        except Exception as e:
            log.warning("clip warmup failed: %s", e)

    async def close(self) -> None:
        self.closed = True
        await self._cancel_present()
        await self._cancel_qa()
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
            if self.state == S.PAUSED:
                await self._resume_from_pause("user")
            elif self.state in (S.LISTENING, S.ANSWERING) and self._qa_task is not None:
                await self._abort_qa_and_resume()
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

    async def _cancel_qa(self) -> None:
        t, self._qa_task = self._qa_task, None
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
        await self._cancel_qa()
        self.tracker.cancel_all()
        self.buf.reset()
        self._ptt_active = False
        await self._send(M.Pause(fade_ms=100, reason="restart"))
        self.playhead = (self.plan.first, 0)
        if self.state != S.IDLE:
            await self._go(S.IDLE, "restart")
        await self._begin_presenting("restart")

    # ------------------------------------------------------------------ presenting
    async def _present_loop(self) -> None:
        assert self.plan is not None
        plan = self.plan
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
        self.echo.speaking(text)
        try:
            box = self._cursor_map.get(str(n), [])[i]
        except (IndexError, TypeError):
            box = None
        await self._send(M.PlaySentence(play_id=play_id, slide_n=n, sentence_i=i, text=text,
                                        url=self._sentence_url(n, i), next_url=next_url, pointer=self._pointer(box)))
        await self.tracker.wait(play_id, fut, audio_timeout(path.stat().st_size))
        self.echo.mark_end(time.monotonic())

    async def _finish_deck(self) -> None:
        await self._go(S.END, "deck_finished")
        await self._send(self._summary())
        await self._play_clip("open_qa", wait=True)
        self.buf.reset()
        self.buf.capturing = True
        await self._go(S.OPEN_QA, "open_qa")
        self._start_qa("open")

    def _summary(self) -> M.Summary:
        return M.Summary(questions=[{"question": r["question"], "answer": r["answer"], "ts": r["ts"]}
                                    for r in self.interruptions],
                         unanswered=list(self.unanswered))

    # ------------------------------------------------------------------ clips
    async def _play_clip(self, key: str, wait: bool = False):
        url = await ensure_clip(self.svc.audio, self.cfg.stock_dir, key, self.cfg.presenter_name)
        text = clip_text(key, self.cfg.presenter_name)
        pid = self.tracker.new_id("c")
        fut = self.tracker.expect(pid)
        self.echo.speaking(text)
        await self._send(M.PlayClip(play_id=pid, clip=key, text=text, url=url))
        if wait:
            await self.tracker.wait(pid, fut, 8.0)
            self.echo.mark_end(time.monotonic())
        return pid, fut

    # ================================================================== STT / barge-in
    async def _on_sim(self, m: M.SimTranscript) -> None:
        await self._on_stt_event(STTEvent("transcript", m.text, m.is_final, m.confidence))
        if m.utterance_end:
            await self._on_stt_event(STTEvent("utterance_end"))

    async def _on_stt_event(self, ev: STTEvent) -> None:
        now = time.monotonic()
        if ev.kind == "transcript":
            await self._send(M.Transcript(text=ev.text, is_final=ev.is_final, confidence=ev.confidence))
            await self._on_transcript(ev.text, ev.is_final, ev.confidence, now)
        elif ev.kind == "utterance_end":
            await self._on_utterance_end(now)

    async def _on_transcript(self, text: str, is_final: bool, conf: float, now: float) -> None:
        st = self.state
        if st == S.PRESENTING:
            if not self.barge_in_enabled:
                return
            d = self.detector.check(text, conf, received_at=now)
            if d is not None:
                await self._barge_in(d, text)
            elif self.detector.last_reject:
                rej, self.detector.last_reject = self.detector.last_reject, None
                self._debug({"type": "barge_in_ignored", "trigger": rej[0], "reason": rej[1], "text": text})
        elif st in (S.LISTENING, S.OPEN_QA):
            if not self.buf.capturing and st == S.OPEN_QA:
                self.buf.capturing = True
            if self.echo.is_echo(text):
                return  # the agent's own voice leaking into the mic
            self.buf.add(text, is_final)
            self._qa_q.put_nowait(("speech",))

    async def _on_utterance_end(self, now: float) -> None:
        st = self.state
        if st == S.PRESENTING:
            self.buf.reset()
            return
        if st not in (S.LISTENING, S.OPEN_QA) or self._ptt_active:
            return
        raw = self.buf.text()
        self.buf.reset()
        self.buf.capturing = True
        self._push_question(raw, now)

    def _push_question(self, raw: str, t_end: float) -> bool:
        if not raw or self.echo.is_echo(raw):
            return False
        q = strip_leading_trigger(raw)
        if len(q.split()) < self.cfg.utterance_min_words:
            return False  # lone trigger phrase ("I have a question") or noise
        self._qa_q.put_nowait(("question", q, t_end))
        return True

    async def _barge_in(self, d: BargeDecision, text: str) -> None:
        m = d.match
        self.last_trigger = m.trigger
        log.info("barge-in %r score=%.0f detect=%.1fms inline=%s", m.trigger, m.score, d.detect_ms, d.treat_as_question)
        self.buf.seed_from_trigger(text, m.start_char)
        await self._send(M.BargeInHit(trigger=m.trigger, text=text, score=m.score, detect_ms=round(d.detect_ms, 2)))
        await self._pause("barge_in")  # pause message goes to the client before anything else
        self._start_qa("interrupt", skip_go_ahead=d.treat_as_question, trigger=m.trigger)

    async def _on_hand_raise(self) -> None:
        if self.state not in (S.PRESENTING, S.PAUSED) or self._qa_task is not None:
            return
        await self._send(M.BargeInHit(trigger="hand_raise", text="", score=100.0, detect_ms=0.0, source="hand_raise"))
        if self.state == S.PRESENTING:
            await self._pause("hand_raise")
        self.buf.reset()
        self.buf.capturing = True
        self._start_qa("interrupt", trigger="hand_raise")

    async def _on_ptt(self, active: bool) -> None:
        if active:
            if self.state in (S.PRESENTING, S.PAUSED) and self._qa_task is None:
                self._ptt_active = True
                await self._send(M.BargeInHit(trigger="push_to_talk", text="", score=100.0, detect_ms=0.0, source="ptt"))
                if self.state == S.PRESENTING:
                    await self._pause("ptt")
                self.buf.reset()
                self.buf.capturing = True
                self._start_qa("interrupt", skip_go_ahead=True, trigger="push_to_talk")
            elif self.state in (S.LISTENING, S.OPEN_QA):
                self._ptt_active = True
        elif self._ptt_active:
            self._ptt_active = False
            await self.stt.finalize()
            await asyncio.sleep(0.35)  # let the final transcript land
            raw = self.buf.text()
            self.buf.reset()
            self.buf.capturing = True
            self._push_question(raw, time.monotonic())

    # ================================================================== Q&A flow
    def _start_qa(self, mode: str, skip_go_ahead: bool = False, trigger: str = "") -> None:
        self._qa_q = asyncio.Queue()
        if skip_go_ahead:
            self._qa_q.put_nowait(("speech",))
        self._qa_task = asyncio.create_task(self._qa_loop(mode, skip_go_ahead, trigger), name=f"qa-{mode}")

    async def _next_question(self, first_timeout: float | None):
        """Wait for a question. Returns (text, t_end) or None on silence/timeout."""
        timeout = first_timeout
        while True:
            try:
                item = await asyncio.wait_for(self._qa_q.get(), timeout)
            except asyncio.TimeoutError:
                return None
            if item[0] == "speech":
                timeout = self.cfg.utterance_max_s
            elif item[0] == "question":
                return item[1], item[2]

    async def _qa_loop(self, mode: str, skip_go_ahead: bool, trigger: str) -> None:
        try:
            if mode == "interrupt":
                await self._go(S.LISTENING, trigger or "barge_in")
                if not skip_go_ahead:
                    await self._play_clip("go_ahead")  # don't block: the user may already be talking
                got = await self._next_question(self.cfg.listen_timeout_s)
                while got is not None:
                    q, t_end = got
                    await self._answer(q, t_end, trigger)
                    if self._resume_requested:
                        break
                    await self._play_clip("anything_else", wait=True)
                    got = await self._next_question(self.cfg.followup_wait_s)
                    if got is not None and is_dismissal(got[0]):
                        got = None
                if not self._resume_requested or got is None:
                    await self._play_clip("continue", wait=True)
                await self._resume_after_qa()
            else:  # OPEN_QA: keep answering until the session ends
                while True:
                    got = await self._next_question(None)
                    if got is None:
                        continue
                    await self._answer(got[0], got[1], "open_qa")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Q&A loop crashed")
            if mode == "interrupt" and not self.closed:
                await self._resume_after_qa()

    async def _abort_qa_and_resume(self) -> None:
        await self._cancel_qa()
        self.tracker.cancel_all()
        await self._send(M.Pause(fade_ms=150, reason="skip_qa"))
        await self._resume_after_qa()

    async def _resume_after_qa(self) -> None:
        """Back to the slide we interrupted, restarting the interrupted sentence from its beginning."""
        self._qa_task = None if self._qa_task is asyncio.current_task() else self._qa_task
        self._resume_requested = False
        self._ptt_active = False
        self.buf.reset()
        await self._begin_presenting("qa_done")

    async def _answer(self, question: str, t_end: float, trigger: str) -> None:
        assert self.plan is not None
        resume_state = self.state
        self._answer_slide = self.playhead[0]
        await self._go(S.ANSWERING, "question")
        self.buf.reset()
        self._resume_requested = False
        engine = QAEngine(self.svc.llm, self.cfg)
        chunks = await asyncio.to_thread(self.svc.kb.search, self.deck_id, question, 3)
        context = format_context(chunks)
        cur = self.playhead[0]
        hint = f"slide {cur}: {self.plan.title(cur)}"
        messages = qa_messages(self.cfg.presenter_name, question, context, self._history, hint)

        send_q: asyncio.Queue = asyncio.Queue()
        last_play: list = []  # (play_id, future) of the last audio sent
        first_audio: list[float] = []
        idx = 0

        async def sender() -> None:
            while True:
                item = await send_q.get()
                if item is None:
                    return
                text, task = item
                try:
                    (audio, _prov), shown, pointer = await task
                except Exception as e:
                    log.warning("TTS failed for answer sentence: %s", e)
                    continue
                pid = self.tracker.new_id("a")
                fut = self.tracker.expect(pid)
                self.echo.speaking(text)
                if not first_audio:
                    first_audio.append(time.monotonic())
                await self._send(M.PlayAnswer(play_id=pid, index=len(last_play), text=text,
                                              audio_b64=base64.b64encode(audio).decode(), slide_n=shown, pointer=pointer))
                last_play.append((pid, fut))

        sender_task = asyncio.create_task(sender(), name="answer-sender")

        source_slides = tuple(dict.fromkeys(c.slide_n for c in chunks if c.slide_n > 0))

        async def synth_with_pointer(text: str, shown: int):
            # TTS and the pointer lookup (embedding) run side by side; neither delays the other
            audio, (slide, pointer) = await asyncio.gather(
                self.svc.tts.synth(text), asyncio.to_thread(self._match_pointer, shown, text, source_slides))
            self._answer_slide = slide
            return audio, slide, pointer

        def speak(text: str) -> None:
            shown = self._answer_slide or self.playhead[0]
            send_q.put_nowait((text, asyncio.create_task(synth_with_pointer(text, shown))))

        streamer = SentenceStreamer()
        toolbox = SessionToolbox(self)
        fin: Finished | None = None
        failed: Failed | None = None
        fallback_used = False
        filler_at: float | None = None
        try:
            async for ev in engine.answer(messages, toolbox, t0=t_end):
                if isinstance(ev, Filler):
                    filler_at = time.monotonic()
                    await self._play_clip("filler")
                elif isinstance(ev, Text):
                    for s in streamer.feed(ev.text):
                        speak(s)
                elif isinstance(ev, ModelSwitch):
                    fallback_used = True
                    await self._send(M.ModelInfo(model=ev.model, fallback_used=True, question=question))
                elif isinstance(ev, Finished):
                    fin = ev
                    for s in streamer.flush():
                        speak(s)
                elif isinstance(ev, Failed):
                    failed = ev
        finally:
            send_q.put_nowait(None)
            await asyncio.gather(sender_task, return_exceptions=True)

        answer_text = fin.text.strip() if fin else ""
        unanswered = failed is not None or not answer_text or bool(FOLLOWUP_RE.search(answer_text))
        if failed is not None or not answer_text:
            await self._play_clip("followup")  # canned clip
            answer_text = answer_text or clip_text("followup", self.cfg.presenter_name)
        if last_play:
            await self.tracker.wait(last_play[-1][0], last_play[-1][1], 25.0)
            self.echo.mark_end(time.monotonic())

        total_ms = (time.monotonic() - t_end) * 1000
        marks = [t for t in (filler_at, first_audio[0] if first_audio else None) if t]
        first_audio_ms = (min(marks) - t_end) * 1000 if marks else None
        model = fin.model if fin else ("canned-clip" if failed else "")
        info = M.ModelInfo(model=model, first_token_ms=round(fin.first_token_ms, 1) if fin and fin.first_token_ms else
                           (round(failed.first_token_ms, 1) if failed and failed.first_token_ms else None),
                           first_audio_ms=round(first_audio_ms, 1) if first_audio_ms is not None else None,
                           total_ms=round(total_ms, 1), fallback_used=fallback_used or (fin.fallback_used if fin else False),
                           question=question)
        self.last_model_info = info.wire()
        await self._send(info)
        record = {"ts": now_iso(), "session_id": self.session_id, "deck_id": self.deck_id, "slide_n": self.playhead[0],
                  "sentence_i": self.playhead[1], "trigger": trigger, "question": question, "answer": answer_text,
                  "model": model, "first_token_ms": info.first_token_ms, "first_audio_ms": info.first_audio_ms,
                  "total_ms": info.total_ms, "fallback": info.fallback_used, "unanswered": unanswered}
        self.interruptions.append(record)
        self._history.append((question, answer_text))
        try:
            self.svc.logs.add_interruption(self.session_id, record)
            if unanswered:
                entry = {"ts": record["ts"], "deck_id": self.deck_id, "session_id": self.session_id,
                         "slide_n": record["slide_n"], "question": question}
                self.unanswered.append(entry)
                self.svc.logs.add_unanswered(entry)
        except Exception as e:  # logging must never break the talk
            log.warning("log write failed: %s", e)
        await self._send(self._summary())

        # back to listening (interrupt flow continues in _qa_loop; open Q&A returns to OPEN_QA)
        self.buf.reset()
        self.buf.capturing = True
        while not self._qa_q.empty():  # drop stale markers captured while answering
            self._qa_q.get_nowait()
        target = S.OPEN_QA if resume_state == S.OPEN_QA else S.LISTENING
        await self._go(target, "answered")
