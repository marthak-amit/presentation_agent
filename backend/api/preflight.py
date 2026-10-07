"""Setup check: verifies every external dependency with a real (tiny) request so problems show up BEFORE the demo."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass, field

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..stt.deepgram import build_url
from ..tts.el_errors import explain, parse_error

router = APIRouter()


@dataclass
class Check:
    id: str
    label: str
    status: str = "ok"  # ok | warn | fail | mock
    detail: str = ""
    fix: str = ""
    ms: int | None = None
    extra: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status, "detail": self.detail, "fix": self.fix,
                "ms": self.ms, **self.extra}


async def _timed(coro, check: Check, timeout: float = 9.0) -> Check:
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError:
        check.status, check.detail = "fail", f"timed out after {timeout:.0f}s (network blocked?)"
    except Exception as e:
        check.status, check.detail = "fail", f"{type(e).__name__}: {str(e)[:160]}"
    check.ms = int((time.monotonic() - t0) * 1000)
    return check


# --------------------------------------------------------------------------- individual checks
def _el_status(r: httpx.Response) -> str:
    """ElevenLabs error bodies look like {"detail": {"status": "invalid_api_key" | "missing_permissions", ...}}."""
    try:
        d = r.json().get("detail")
        return (d.get("status") if isinstance(d, dict) else str(d or "")) or ""
    except Exception:
        return ""


async def check_tools() -> list[Check]:
    out = []
    lo = shutil.which("soffice") or shutil.which("libreoffice")
    c = Check("libreoffice", "LibreOffice (slide rendering)")
    if lo:
        try:
            v = subprocess.run([lo, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            v = "found"
        c.detail = v or "found"
    else:
        c.status, c.detail = "warn", "not found - slides will be grey placeholders"
        c.fix = "macOS: brew install --cask libreoffice · Ubuntu: apt install libreoffice-impress"
    out.append(c)
    c = Check("poppler", "Poppler (pdftoppm / pdftotext)")
    if shutil.which("pdftoppm") and shutil.which("pdftotext"):
        c.detail = "found"
    else:
        c.status, c.detail = "warn", "not found - slides will be grey placeholders and the highlighter has no positions"
        c.fix = "macOS: brew install poppler · Ubuntu: apt install poppler-utils"
    out.append(c)
    return out


async def check_groq(svc) -> Check:
    s = svc.settings
    c = Check("groq", "Groq (narration + answers)")
    if not s.use_real_llm:
        c.status, c.detail = "mock", "no GROQ_API_KEY - using the offline mock LLM"
        c.fix = "Add GROQ_API_KEY to .env (console.groq.com)"
        return c

    async def run():
        async with httpx.AsyncClient(timeout=8.0) as cl:
            r = await cl.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {s.groq_api_key}"})
        if r.status_code in (401, 403):
            c.status, c.detail, c.fix = "fail", f"API key rejected (HTTP {r.status_code})", "Create a new key at console.groq.com"
            return
        r.raise_for_status()
        ids = {m["id"] for m in r.json().get("data", [])}
        missing = [m for m in (s.groq_qa_model, s.groq_script_model, s.groq_fallback_model) if m and m not in ids]
        if missing:
            skip = ("whisper", "guard", "tts", "playai", "orpheus", "distil")
            chat = sorted(i for i in ids if not any(x in i.lower() for x in skip))
            c.status = "fail"
            c.detail = f"key OK, but model(s) not available to this key: {', '.join(missing)}"
            c.fix = ("Set GROQ_FALLBACK_MODEL (and any other missing one) in .env to a model your key has. Available: "
                     + ", ".join(chat[:12]))
            c.extra["available_models"] = chat
        else:
            c.detail = f"key OK · QA {s.groq_qa_model} · fallback {s.groq_fallback_model}"

    return await _timed(run(), c)


async def check_deepgram(svc) -> list[Check]:
    s = svc.settings
    stt = Check("deepgram", "Deepgram (speech recognition)")
    if not s.use_real_stt:
        stt.status, stt.detail = "mock", "no DEEPGRAM_API_KEY - voice commands unavailable (use the simulate box)"
        stt.fix = "Add DEEPGRAM_API_KEY to .env (console.deepgram.com)"
        return [stt]
    from ..session.barge_in import ALL_TRIGGERS

    async def run():
        url = build_url(s, ALL_TRIGGERS)
        try:
            async with websockets.connect(url, additional_headers={"Authorization": f"Token {s.deepgram_api_key}"},
                                          open_timeout=7):
                stt.detail = "live connection OK (multilingual, keyterms, interim results)"
        except websockets.exceptions.InvalidStatus as e:
            code = e.response.status_code
            if code in (401, 403):
                stt.status, stt.detail = "fail", f"API key rejected (HTTP {code})"
                stt.fix = "Create a new key at console.deepgram.com"
            else:
                stt.status = "warn"
                stt.detail = f"full options refused (HTTP {code}); the app will fall back to simpler settings automatically"

    await _timed(run(), stt)
    return [stt]


async def check_tts(svc) -> Check:
    s = svc.settings
    c = Check("tts", "Text to speech (voice)")
    if s.use_elevenlabs:
        c.label = "ElevenLabs (your cloned voice)"

        async def run():
            h = {"xi-api-key": s.elevenlabs_api_key}
            async with httpx.AsyncClient(timeout=10.0) as cl:
                name = ""
                r = await cl.get(f"https://api.elevenlabs.io/v1/voices/{s.elevenlabs_voice_id}", headers=h)
                if r.status_code == 200:
                    j = r.json()
                    name = f"voice '{j.get('name', '?')}' ({j.get('category', 'voice')})"
                    c.extra["voice"] = j.get("name", "")
                elif r.status_code != 401 or _el_status(r) != "missing_permissions":
                    status, message = parse_error(r.status_code, r.content)
                    c.status = "fail"
                    c.detail, c.fix = explain(r.status_code, status, message)
                    return
                # Knowing the voice exists proves nothing: library voices, quota or plan limits only show up when
                # speech is actually generated, and the app would then silently fall back to another voice.
                t = await cl.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}",
                                  headers={**h, "accept": "audio/mpeg"}, json={"text": "Hi.", "model_id": s.elevenlabs_model_id})
                if t.status_code == 200 and len(t.content) > 200:
                    c.detail = (f"{name or 'key + voice id work'} · speech generated OK · model {s.elevenlabs_model_id}"
                                + ("" if name else " · (voice name hidden: key lacks 'Voices: read')"))
                else:
                    status, message = parse_error(t.status_code, t.content)
                    c.status = "fail"
                    what, fix = explain(t.status_code, status, message)
                    c.detail = f"{name + ' found, but ' if name else ''}speech generation FAILED: {what}"
                    c.fix = fix
                    c.extra["http"] = t.status_code

        return await _timed(run(), c)
    if s.use_aura:
        c.label = "Deepgram Aura (fallback voice)"
        c.status = "warn"
        c.detail = f"no ElevenLabs key/voice id - using Aura voice {s.deepgram_tts_model} (not your voice)"
        c.fix = "Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID for your cloned voice"
        return c
    c.status, c.detail = "mock", "no TTS keys - slides play with silent mock audio"
    c.fix = "Set ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID (or DEEPGRAM_API_KEY for Aura)"
    return c


async def check_embeddings(svc) -> Check:
    c = Check("embeddings", "Embeddings (answer retrieval)")

    def load():
        return svc.kb.embedder.name

    try:
        name = await asyncio.to_thread(load)
    except Exception as e:
        c.status, c.detail = "fail", str(e)[:160]
        return c
    if name.startswith("hash"):
        c.status = "warn"
        c.detail = "built-in hashing embedder (lower quality) - MiniLM could not be loaded"
        c.fix = "pip install sentence-transformers and make sure huggingface.co is reachable once"
    else:
        c.detail = name
    return c


async def check_env_fresh(svc) -> Check:
    from ..config import ROOT

    c = Check("env", ".env loaded")
    env = ROOT / ".env"
    started = getattr(svc, "started_at", None)
    if not env.exists():
        c.status, c.detail, c.fix = "warn", "no .env file - using .env.example defaults", "cp .env.example .env and add your keys"
    elif started and env.stat().st_mtime > started + 1:
        c.status = "warn"
        c.detail = ".env was edited AFTER the server started, so the running server still uses the OLD keys/voice"
        c.fix = "Stop make dev (Ctrl-C) and start it again"
    else:
        c.detail = ".env is up to date with the running server"
    return c


async def check_storage(svc) -> Check:
    c = Check("storage", "Data folder")
    try:
        p = svc.settings.data_dir / ".write_test"
        p.write_text("ok")
        p.unlink()
        c.detail = str(svc.settings.data_dir)
    except Exception as e:
        c.status, c.detail = "fail", f"not writable: {e}"
    return c


@router.get("/preflight")
async def preflight(request: Request):
    svc = request.app.state.svc
    tools, groq, dg, tts, emb, store, env = await asyncio.gather(
        check_tools(), check_groq(svc), check_deepgram(svc), check_tts(svc), check_embeddings(svc), check_storage(svc),
        check_env_fresh(svc))
    checks = [env, *tools, groq, *dg, tts, emb, store]
    worst = "ok"
    for c in checks:
        if c.status == "fail":
            worst = "fail"
        elif c.status in ("warn", "mock") and worst == "ok":
            worst = "warn"
    return {"overall": worst, "presenter": svc.settings.presenter_name, "checks": [c.dict() for c in checks]}


@router.get("/preflight/voice")
async def voice_sample(request: Request, text: str = ""):
    """A short sentence in the configured voice, for the 'Play voice test' button."""
    svc = request.app.state.svc
    name = svc.settings.presenter_name
    sample = (text or f"Hi everyone, I am {name}. This is a quick voice test before we begin.")[:200]
    primary = svc.tts.providers[0]
    err = ""
    try:  # test the configured voice directly (ignoring the failure cool-down) so the real error is visible
        if primary.name == "fake":
            raise RuntimeError("no TTS keys configured")
        audio, provider = await primary.synth(sample), primary.name
    except Exception as e:
        err = str(e)
        audio, provider = await svc.tts.synth(sample)  # what the audience would hear instead
    err = err.encode("ascii", "replace").decode()[:300]
    return Response(audio, media_type="audio/mpeg",
                    headers={"X-TTS-Provider": provider, "X-TTS-Error": err, "X-TTS-Primary": getattr(svc.tts, "primary_real", ""),
                             "Cache-Control": "no-store"})
