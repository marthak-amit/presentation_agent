"""WebSocket endpoints: /ws/session (presenter client) and /ws/debug (live debug feed, phase 4)."""
from __future__ import annotations

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
            await session.close()
