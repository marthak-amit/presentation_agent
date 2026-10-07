"""REST: session logs (interruptions) and unanswered questions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..session.report import build_report

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


@router.get("/sessions/{session_id}/export.md", response_class=PlainTextResponse)
async def export_session(session_id: str, request: Request):
    """Markdown Q&A report for a session (questions, answers, follow-ups)."""
    if not session_id.isalnum():
        raise HTTPException(400, "bad session id")
    svc = request.app.state.svc
    rows = svc.logs.read_session(session_id)
    name = ""
    if rows and svc.store.exists(rows[0].get("deck_id", "")):
        name = svc.store.meta(rows[0]["deck_id"]).get("name", "")
    body = build_report(session_id, name, svc.settings.presenter_name, rows)
    return PlainTextResponse(body, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="qa-report-{session_id}.md"'})
