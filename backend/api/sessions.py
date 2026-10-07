"""REST: session logs (interruptions) and unanswered questions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/sessions")
async def list_sessions(request: Request):
    svc = request.app.state.svc
    files = sorted((svc.settings.logs_dir / "sessions").glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    live = set(svc.hub.sessions)
    return [{"session_id": p.stem, "live": p.stem in live, "interruptions": len(svc.logs.read_session(p.stem))}
            for p in files[:50]]


@router.get("/sessions/{session_id}/log")
async def session_log(session_id: str, request: Request):
    if not session_id.isalnum():
        raise HTTPException(400, "bad session id")
    return request.app.state.svc.logs.read_session(session_id)


@router.get("/logs/unanswered")
async def unanswered(request: Request):
    return request.app.state.svc.logs.unanswered()
