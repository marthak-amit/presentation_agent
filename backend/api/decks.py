"""REST: decks upload / status / docs."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ..ingest.docs import ALLOWED, extract_text
from ..ingest.pipeline import run_ingest

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
                f"/media/{deck_id}/audio/{n}/s{i}.mp3" if store.audio_path(deck_id, n, i).exists() else None
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
        "slides": out,
    }


@router.post("/decks")
async def create_deck(request: Request, file: UploadFile = File(...)):
    svc = _svc(request)
    fname = file.filename or "deck.pptx"
    if not fname.lower().endswith(".pptx"):
        raise HTTPException(400, "Only .pptx files are supported")
    data = await file.read()
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(400, "Empty or too large file")
    deck_id = svc.store.create(Path(fname).stem)
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
