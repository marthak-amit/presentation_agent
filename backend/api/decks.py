"""REST: decks upload / status / docs."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..ingest.docs import ALLOWED, extract_text
from ..ingest.pipeline import run_ingest
from ..ingest.script import ScriptError, clean_sentences, regenerate_slide_script, save_slide_script
from ..llm.prompts import LENGTHS, TONES

router = APIRouter()
MAX_UPLOAD = 200 * 1024 * 1024


def _svc(request: Request):
    return request.app.state.svc


def deck_payload(svc, deck_id: str) -> dict:
    store = svc.store
    meta = store.meta(deck_id)
    slides = store.slides(deck_id)
    narr = {n["n"]: n for n in store.narration(deck_id)}
    out = []
    for s in slides:
        n = s["n"]
        sentences = narr.get(n, {}).get("sentences", [])
        out.append({
            "n": n,
            "image_url": f"/media/{deck_id}/slides/{n}.png",
            "title": s["title"],
            "sentences": sentences,
            "audio_urls": [
                f"/media/{deck_id}/audio/{n}/s{i}.mp3?v={store.audio_path(deck_id, n, i).stat().st_mtime_ns // 1000}"
                if store.audio_path(deck_id, n, i).exists() else None
                for i in range(len(sentences))
            ],
        })
    return {
        "deck_id": deck_id,
        "name": meta.get("name", ""),
        "status": meta.get("status", "unknown"),
        "stage": meta.get("stage", ""),
        "progress": meta.get("progress", 0),
        "error": meta.get("error"),
        "audio": meta.get("audio", {"done": 0, "total": 0}),
        "docs": meta.get("docs", []),
        "tone": meta.get("tone", "conversational"),
        "length": meta.get("length", "standard"),
        "slides": out,
    }


@router.post("/decks")
async def create_deck(request: Request, file: UploadFile = File(...), tone: str = Form("conversational"),
                      length: str = Form("standard")):
    svc = _svc(request)
    if tone not in TONES or length not in LENGTHS:
        raise HTTPException(400, f"tone must be one of {list(TONES)}, length one of {list(LENGTHS)}")
    fname = file.filename or "deck.pptx"
    if not fname.lower().endswith(".pptx"):
        raise HTTPException(400, "Only .pptx files are supported")
    data = await file.read()
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(400, "Empty or too large file")
    deck_id = svc.store.create(Path(fname).stem, tone, length)
    svc.store.source_path(deck_id).write_bytes(data)
    task = asyncio.create_task(run_ingest(svc, deck_id))
    tasks: set = request.app.state.tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"deck_id": deck_id, "status": "processing"}


@router.get("/decks")
async def list_decks(request: Request):
    svc = _svc(request)
    return [
        {k: svc.store.meta(d).get(k) for k in ("deck_id", "name", "status", "stage", "progress", "slide_count")}
        for d in svc.store.list_ids()
    ]


@router.get("/decks/{deck_id}")
async def get_deck(deck_id: str, request: Request):
    svc = _svc(request)
    if not svc.store.exists(deck_id):
        raise HTTPException(404, "deck not found")
    return deck_payload(svc, deck_id)


@router.post("/decks/{deck_id}/docs")
async def add_docs(deck_id: str, request: Request, files: list[UploadFile] = File(...)):
    svc = _svc(request)
    if not svc.store.exists(deck_id):
        raise HTTPException(404, "deck not found")
    added = []
    total_chunks = 0
    for f in files:
        name = f.filename or "document.txt"
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED:
            raise HTTPException(400, f"Unsupported file type {ext}; use pdf, txt or md")
        text = extract_text(name, await f.read())
        if not text.strip():
            raise HTTPException(400, f"No extractable text in {name}")
        n = await asyncio.to_thread(svc.kb.index_document, deck_id, name, text)
        total_chunks += n
        added.append({"name": name, "chunks": n})
    meta = svc.store.meta(deck_id)
    meta["docs"] = [d for d in meta.get("docs", []) if d["name"] not in {a["name"] for a in added}] + added
    svc.store.write_meta(deck_id, meta)
    return {"deck_id": deck_id, "added": added, "chunks": total_chunks}


@router.get("/decks/{deck_id}/search")
async def search(deck_id: str, q: str, request: Request, k: int = 3):
    svc = _svc(request)
    if not svc.store.exists(deck_id):
        raise HTTPException(404, "deck not found")
    chunks = await asyncio.to_thread(svc.kb.search, deck_id, q, k)
    return [{"text": c.text, "slide_n": c.slide_n, "source": c.source, "distance": c.distance} for c in chunks]


@router.post("/decks/{deck_id}/audio")
async def regenerate_audio(deck_id: str, request: Request):
    """(Re)run audio pre-generation; cached files are never regenerated."""
    from ..tts.pregen import pregenerate_deck_audio

    svc = _svc(request)
    if not svc.store.exists(deck_id) or svc.store.meta(deck_id).get("status") != "ready":
        raise HTTPException(404, "deck not ready")
    task = asyncio.create_task(pregenerate_deck_audio(svc, deck_id))
    tasks: set = request.app.state.tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"deck_id": deck_id, "status": "started"}


class ScriptEdit(BaseModel):
    sentences: list[str] | None = None
    text: str | None = None


class Regenerate(BaseModel):
    instruction: str = ""
    tone: str | None = None
    length: str | None = None


def _slide_payload(svc, deck_id: str, n: int) -> dict:
    d = deck_payload(svc, deck_id)
    return next(s for s in d["slides"] if s["n"] == n)


def _need_ready_slide(svc, deck_id: str, n: int) -> None:
    if not svc.store.exists(deck_id):
        raise HTTPException(404, "deck not found")
    if svc.store.meta(deck_id).get("status") != "ready":
        raise HTTPException(409, "deck is still processing")
    if not any(x["n"] == n for x in svc.store.narration(deck_id)):
        raise HTTPException(404, "slide not found")


def _background(request: Request, coro) -> None:
    task = asyncio.create_task(coro)
    tasks: set = request.app.state.tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.put("/decks/{deck_id}/narration/{n}")
async def edit_narration(deck_id: str, n: int, body: ScriptEdit, request: Request):
    """Replace the script of one slide (list of sentences, or free text that is split into sentences)."""
    from ..tts.pregen import pregenerate_deck_audio

    svc = _svc(request)
    _need_ready_slide(svc, deck_id, n)
    try:
        sentences = clean_sentences(body.sentences, body.text)
    except ScriptError as e:
        raise HTTPException(400, str(e))
    await save_slide_script(svc, deck_id, n, sentences)
    _background(request, pregenerate_deck_audio(svc, deck_id))  # only changed sentences are synthesised
    return _slide_payload(svc, deck_id, n)


@router.post("/decks/{deck_id}/narration/{n}/regenerate")
async def regenerate_narration(deck_id: str, n: int, body: Regenerate, request: Request):
    from ..tts.pregen import pregenerate_deck_audio

    svc = _svc(request)
    _need_ready_slide(svc, deck_id, n)
    if (body.tone and body.tone not in TONES) or (body.length and body.length not in LENGTHS):
        raise HTTPException(400, "unknown tone or length")
    try:
        await regenerate_slide_script(svc, deck_id, n, body.instruction, body.tone, body.length)
    except ScriptError as e:
        raise HTTPException(502, f"the model returned an unusable script: {e}")
    _background(request, pregenerate_deck_audio(svc, deck_id))
    return _slide_payload(svc, deck_id, n)


@router.delete("/decks/{deck_id}")
async def delete_deck(deck_id: str, request: Request):
    svc = _svc(request)
    if not svc.store.exists(deck_id):
        raise HTTPException(404, "deck not found")
    try:
        await asyncio.to_thread(svc.kb.delete_deck, deck_id)
    except Exception:
        pass
    svc.store.delete(deck_id)
    return {"deleted": deck_id}
