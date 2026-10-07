"""Deepgram live streaming STT over websockets (interim results, utterance end, keyterms, auto-reconnect)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from urllib.parse import urlencode

import websockets

from ..config import Settings
from .base import OnSTTEvent, STTEvent

log = logging.getLogger("stt.deepgram")


DEFAULT_BASE = "wss://api.deepgram.com/v1/listen"


def build_url(settings: Settings, keyterms: list[str], language: str = "multi", base: str = DEFAULT_BASE) -> str:
    params: list[tuple[str, str]] = [
        ("model", settings.deepgram_stt_model),
        ("language", language),
        ("smart_format", "true"),
        ("interim_results", "true"),
        ("utterance_end_ms", "1000"),
        ("vad_events", "true"),
        ("endpointing", "300"),
        ("encoding", "linear16"),
        ("sample_rate", "16000"),
        ("channels", "1"),
    ]
    params += [("keyterm", k) for k in keyterms]
    return base + "?" + urlencode(params)


def parse_message(raw: str | bytes) -> STTEvent | None:
    try:
        msg = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    t = msg.get("type")
    if t == "Results":
        alts = (msg.get("channel") or {}).get("alternatives") or [{}]
        text = (alts[0].get("transcript") or "").strip()
        if not text:
            return None
        return STTEvent("transcript", text, bool(msg.get("is_final")), float(alts[0].get("confidence", 1.0)))
    if t == "UtteranceEnd":
        return STTEvent("utterance_end")
    if t == "SpeechStarted":
        return STTEvent("speech_started")
    return None


class DeepgramSTT:
    name = "deepgram"

    def __init__(self, settings: Settings, on_event: OnSTTEvent, keyterms: list[str], base_url: str = DEFAULT_BASE):
        self.settings = settings
        self.base_url = base_url
        self.last_error: str | None = None
        self.variant = 0
        self.on_event = on_event
        self.keyterms = keyterms
        self.status = "connecting"
        self._queue: deque[bytes] = deque(maxlen=300)  # ~ drop old audio instead of growing while offline
        self._wake = asyncio.Event()
        self._closed = False
        self._task: asyncio.Task | None = None
        self._ws = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="deepgram-stt")

    async def send_audio(self, data: bytes) -> None:
        self._queue.append(data)
        self._wake.set()

    async def finalize(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "Finalize"}))
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _variants(self) -> list[tuple[str, str]]:
        """Connection attempts, most capable first. A parameter Deepgram rejects (HTTP 400) degrades to the next one,
        so a demo never ends up with no speech recognition just because of an optional feature."""
        b = self.base_url
        return [
            ("full (multilingual + keyterms)", build_url(self.settings, self.keyterms, "multi", b)),
            ("multilingual without keyterms", build_url(self.settings, [], "multi", b)),
            ("English without keyterms", build_url(self.settings, [], "en", b)),
        ]

    async def _run(self) -> None:
        backoff = 0.5
        variants = self._variants()
        headers = {"Authorization": f"Token {self.settings.deepgram_api_key}"}
        while not self._closed:
            sender = keep = None
            label, url = variants[min(self.variant, len(variants) - 1)]
            try:
                async with websockets.connect(url, additional_headers=headers, ping_interval=None,
                                              open_timeout=5, max_size=None) as ws:
                    self._ws = ws
                    self.status = "up"
                    self.last_error = None
                    backoff = 0.5
                    log.info("deepgram connected (%s)", label)
                    sender = asyncio.create_task(self._send_loop(ws))
                    keep = asyncio.create_task(self._keepalive(ws))
                    async for raw in ws:
                        ev = parse_message(raw)
                        if ev is not None:
                            try:
                                await self.on_event(ev)
                            except Exception:
                                log.exception("STT event handler failed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (401, 403):
                    self.last_error = f"Deepgram rejected the API key (HTTP {code}) - check DEEPGRAM_API_KEY"
                    backoff = max(backoff, 10.0)
                elif code is not None and 400 <= code < 500 and self.variant < len(variants) - 1:
                    self.variant += 1
                    self.last_error = f"Deepgram refused '{label}' (HTTP {code}); falling back"
                    log.warning(self.last_error)
                    backoff = 0.2
                else:
                    self.last_error = f"Deepgram connection lost: {type(e).__name__} {e}"[:160]
                log.warning("deepgram: %s", self.last_error)
            finally:
                self._ws = None
                self.status = "down"
                for t in (sender, keep):
                    if t:
                        t.cancel()
            if self._closed:
                break
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)

    async def _send_loop(self, ws) -> None:
        while True:
            while self._queue:
                await ws.send(self._queue.popleft())
            self._wake.clear()
            if not self._queue:
                await self._wake.wait()

    async def _keepalive(self, ws) -> None:
        while True:
            await asyncio.sleep(4.0)
            await ws.send(json.dumps({"type": "KeepAlive"}))
