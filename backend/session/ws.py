"""WebSocket endpoints: /ws/session (presenter client) and /ws/debug (live debug feed, phase 4)."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from . import messages as M
from .session import PresenterSession

log = logging.getLogger("ws")
router = APIRouter()


@router.websocket("/ws/session")
async def ws_session(ws: WebSocket):
    await ws.accept()
    svc = ws.app.state.svc
    session: PresenterSession | None = None

    async def send(obj: dict) -> None:
        await ws.send_text(json.dumps(obj))

    try:
        while True:
            packet = await ws.receive()
            if packet["type"] == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                if session is not None:
                    await session.on_audio(packet["bytes"])
                continue
            text = packet.get("text")
            if text is None:
                continue
            try:
                msg = M.parse_client_message(json.loads(text))
            except (ValidationError, json.JSONDecodeError) as e:
                await send(M.ErrorMsg(message=f"bad message: {str(e)[:200]}").wire())
                continue
            if isinstance(msg, M.StartSession):
                if session is not None:
                    await session.close()
                session = PresenterSession(svc, send)
                svc.hub.register(session)
                await session.open(msg.deck_id, msg.barge_in, msg.slide_n, msg.sentence_i)
                continue
            if session is None:
                await send(M.ErrorMsg(message="send start_session first").wire())
                continue
            await session.handle(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws session crashed")
    finally:
        if session is not None:
            svc.hub.unregister(session)
            await session.close()


@router.websocket("/ws/debug")
async def ws_debug(ws: WebSocket):
    """Live feed of every session's events (+ replay of the recent ones). Accepts sim_transcript for routing."""
    await ws.accept()
    hub = ws.app.state.svc.hub
    q = hub.subscribe()
    for item in list(hub.buffer):
        await ws.send_text(json.dumps(item))

    async def pump():
        while True:
            await ws.send_text(json.dumps(await q.get()))

    task = asyncio.create_task(pump())
    try:
        while True:
            raw = json.loads(await ws.receive_text())
            if raw.get("type") == "sim_transcript":
                target = hub.sessions.get(raw.get("session_id", ""))
                if target is None and hub.sessions:
                    target = list(hub.sessions.values())[-1]
                if target is not None:
                    await target.handle(M.parse_client_message({k: v for k, v in raw.items() if k != "session_id"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws debug crashed")
    finally:
        task.cancel()
        hub.unsubscribe(q)


@router.websocket("/ws/stt-test")
async def ws_stt_test(ws: WebSocket):
    """Setup-check helper: mic audio in, live transcripts + wake-phrase detection out (no deck, no LLM)."""
    from ..stt.deepgram import DeepgramSTT
    from .barge_in import ALL_TRIGGERS, WAKE_TRIGGERS, find_trigger

    await ws.accept()
    settings = ws.app.state.svc.settings
    out: asyncio.Queue = asyncio.Queue()

    async def on_event(ev):
        if ev.kind == "transcript":
            m = find_trigger(ev.text)
            await out.put({"type": "transcript", "text": ev.text, "is_final": ev.is_final, "confidence": ev.confidence,
                           "trigger": m.trigger if m else None, "wake": bool(m and m.trigger in WAKE_TRIGGERS)})

    if not settings.use_real_stt:
        await ws.send_text(json.dumps({"type": "mock", "message": "No DEEPGRAM_API_KEY: speech recognition is mocked."}))
        await ws.close()
        return
    stt = DeepgramSTT(settings, on_event, ALL_TRIGGERS, **({"base_url": settings.deepgram_ws_base} if settings.deepgram_ws_base else {}))
    await stt.start()

    async def pump():
        last = None
        while True:
            try:
                item = await asyncio.wait_for(out.get(), 1.0)
                await ws.send_text(json.dumps(item))
            except asyncio.TimeoutError:
                pass
            st = (stt.status, stt.last_error)
            if st != last:
                last = st
                await ws.send_text(json.dumps({"type": "status", "status": stt.status, "error": stt.last_error}))

    task = asyncio.create_task(pump())
    try:
        while True:
            packet = await ws.receive()
            if packet["type"] == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                await stt.send_audio(packet["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await stt.close()
