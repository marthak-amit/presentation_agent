"""Voice picker: list the voices of the configured ElevenLabs account, test one, and switch to it live."""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import ROOT
from ..envfile import set_env_var
from ..services import attach_tts
from ..tts.el_errors import explain, parse_error

router = APIRouter()
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")


def _need_key(svc) -> None:
    if not svc.settings.elevenlabs_api_key:
        raise HTTPException(400, {"what": "No ELEVENLABS_API_KEY configured", "fix": "Add your ElevenLabs key to .env and restart make dev"})


@router.get("/voices")
async def list_voices(request: Request):
    """Voices available to this ElevenLabs key. Library voices may appear here but still fail to speak on a free plan -
    use the Test button to find out which ones really work."""
    svc = request.app.state.svc
    _need_key(svc)
    async with httpx.AsyncClient(timeout=12.0) as cl:
        r = await cl.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": svc.settings.elevenlabs_api_key})
    if r.status_code != 200:
        status, message = parse_error(r.status_code, r.content)
        what, fix = explain(r.status_code, status, message)
        if r.status_code == 401 and "permission" in f"{status} {message}".lower():
            what, fix = ("This key may not list voices (it lacks 'Voices: read')",
                         "Paste a voice ID manually below, or enable Voices: read on the key")
        raise HTTPException(502, {"what": what, "fix": fix})
    out = []
    for v in r.json().get("voices", []):
        labels = v.get("labels") or {}
        out.append({"voice_id": v["voice_id"], "name": v.get("name", "?"), "category": v.get("category", ""),
                    "detail": ", ".join(x for x in (labels.get("accent"), labels.get("gender"), labels.get("age"),
                                                     labels.get("descriptive")) if x),
                    "current": v["voice_id"] == svc.settings.elevenlabs_voice_id})
    order = {"cloned": 0, "professional": 0, "generated": 1, "premade": 2}
    out.sort(key=lambda v: (not v["current"], order.get(v["category"], 3), v["name"].lower()))
    return {"current": svc.settings.elevenlabs_voice_id, "voices": out}


class VoiceRef(BaseModel):
    voice_id: str
    text: str = ""


def _check_id(voice_id: str) -> str:
    voice_id = voice_id.strip()
    if not _VOICE_ID.match(voice_id):
        raise HTTPException(400, {"what": "That does not look like a voice ID", "fix": "Voice IDs are ~20 letters/digits, e.g. 21m00Tcm4TlvDq8ikWAM (not the voice's name)"})
    return voice_id


@router.post("/voices/test")
async def test_voice(body: VoiceRef, request: Request):
    """Speak a sentence with ANY voice id (without switching to it). 200 = audio/mpeg; 422 = why it cannot be used."""
    svc = request.app.state.svc
    _need_key(svc)
    vid = _check_id(body.voice_id)
    s = svc.settings
    text = (body.text or f"Hi everyone, I am {s.presenter_name}. This is how I sound.")[:200]
    async with httpx.AsyncClient(timeout=20.0) as cl:
        r = await cl.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": s.elevenlabs_api_key, "accept": "audio/mpeg"},
                          json={"text": text, "model_id": s.elevenlabs_model_id})
    if r.status_code == 200 and len(r.content) > 200:
        return Response(r.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
    status, message = parse_error(r.status_code, r.content)
    what, fix = explain(r.status_code, status, message)
    raise HTTPException(422, {"what": what, "fix": fix, "http": r.status_code})


@router.post("/voices/select")
async def select_voice(body: VoiceRef, request: Request):
    """Make `voice_id` the presenter's voice NOW: saves it to .env, rebuilds the TTS chain and re-voices every deck."""
    from ..ingest.pipeline import POST_HOOKS  # noqa: F401  (import keeps module graph explicit)
    from ..tts.clips import ensure_all_clips
    from ..tts.pregen import pregenerate_deck_audio

    svc = request.app.state.svc
    _need_key(svc)
    vid = _check_id(body.voice_id)
    # prove it can really speak before committing to it (a voice that cannot would silently fall back to Aura)
    await test_voice(VoiceRef(voice_id=vid), request)
    set_env_var(ROOT / ".env", "ELEVENLABS_VOICE_ID", vid)
    svc.settings = replace(svc.settings, elevenlabs_voice_id=vid)
    attach_tts(svc)

    async def revoice():
        try:
            await ensure_all_clips(svc.audio, svc.settings.stock_dir, svc.settings.presenter_name)
            for deck_id in svc.store.list_ids():
                if svc.store.meta(deck_id).get("status") == "ready":
                    await pregenerate_deck_audio(svc, deck_id)
        except Exception as e:
            import logging

            logging.getLogger("voices").warning("re-voicing after voice change failed: %s", e)

    task = asyncio.create_task(revoice())
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)
    decks = [d for d in svc.store.list_ids() if svc.store.meta(d).get("status") == "ready"]
    return {"voice_id": vid, "revoicing_decks": len(decks), "saved_to": ".env"}
