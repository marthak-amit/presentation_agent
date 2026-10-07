from __future__ import annotations

import asyncio
import json

import websockets

from backend.stt.base import STTEvent
from backend.stt.deepgram import DeepgramSTT, build_url, parse_message
from backend.session.barge_in import ALL_TRIGGERS


def test_url_has_required_deepgram_params(settings):
    from dataclasses import replace

    url = build_url(replace(settings, deepgram_stt_model="nova-3"), ["i have a question", "wait"])
    for part in ("interim_results=true", "utterance_end_ms=1000", "language=multi", "smart_format=true",
                 "keyterm=i+have+a+question", "keyterm=wait", "encoding=linear16", "sample_rate=16000"):
        assert part in url
    assert url.count("keyterm=") == 2


def test_parse_messages():
    r = parse_message(json.dumps({"type": "Results", "is_final": True, "channel": {"alternatives": [
        {"transcript": " I have a question ", "confidence": 0.93}]}}))
    assert r == STTEvent("transcript", "I have a question", True, 0.93)
    assert parse_message(json.dumps({"type": "Results", "channel": {"alternatives": [{"transcript": ""}]}})) is None
    assert parse_message('{"type":"UtteranceEnd"}').kind == "utterance_end"
    assert parse_message("not json") is None


async def test_deepgram_client_streams_audio_events_and_reconnects(settings):
    received: list = []
    connections = 0

    async def handler(ws):
        nonlocal connections
        connections += 1
        n = connections
        async for msg in ws:
            if isinstance(msg, bytes):
                received.append(msg)
                if n == 1 and len(received) == 1:
                    await ws.send(json.dumps({"type": "Results", "is_final": False, "channel": {"alternatives": [
                        {"transcript": "i have a", "confidence": 0.8}]}}))
                    await ws.send(json.dumps({"type": "UtteranceEnd"}))
                    await ws.close()  # server drops the connection -> client must reconnect
                    return
            else:
                received.append(msg)

    events: list[STTEvent] = []

    async def on_event(ev):
        events.append(ev)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stt = DeepgramSTT(settings, on_event, ALL_TRIGGERS, url=f"ws://127.0.0.1:{port}/v1/listen")
        await stt.start()
        await stt.send_audio(b"\x01\x00" * 1600)
        for _ in range(100):
            if len(events) >= 2:
                break
            await asyncio.sleep(0.05)
        assert [e.kind for e in events][:2] == ["transcript", "utterance_end"]
        # after the drop, audio sent during the outage is delivered on the new connection
        await stt.send_audio(b"\x02\x00" * 1600)
        for _ in range(100):
            if connections >= 2 and any(isinstance(r, bytes) and r[:1] == b"\x02" for r in received):
                break
            await asyncio.sleep(0.05)
        assert connections >= 2 and any(isinstance(r, bytes) and r[:1] == b"\x02" for r in received)
        assert stt.status == "up"
        await stt.close()
