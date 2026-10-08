"""Turn ElevenLabs HTTP errors into something a person can act on."""
from __future__ import annotations

import json


def parse_error(code: int, body: bytes | str) -> tuple[str, str]:
    """-> (status, message) from {"detail": {"status": ..., "message": ...}} (or a plain string detail)."""
    try:
        d = json.loads(body).get("detail")
    except Exception:
        return "", (body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body))[:160]
    if isinstance(d, dict):
        return str(d.get("status", "")), str(d.get("message", ""))[:200]
    return "", str(d or "")[:200]


def explain(code: int, status: str, message: str) -> tuple[str, str]:
    """-> (what happened, how to fix it)."""
    s = f"{status} {message}".lower()
    if code == 402 or "paid_plan_required" in s or "payment_required" in s or "library voice" in s:
        return ("ElevenLabs refuses this voice through the API: it is a Voice Library voice and your plan cannot use library voices via the API",
                "Use a voice from YOUR account instead: a premade voice (click 'Load my voices' above and Test the premade ones), "
                "a voice you create with Voices → Voice Design (describe the accent you want), or clone your own voice. "
                "Voice IDs copied from the public Voice Library are all refused on a free plan. Or upgrade the ElevenLabs plan")
    if "quota" in s or "credit" in s:
        return ("ElevenLabs character quota / credits used up", "Top up or wait for the monthly reset at elevenlabs.io, or use a different account's key")
    if code == 401 and "permission" in s:
        return ("API key lacks permission", "Edit the key at elevenlabs.io and enable Text to Speech")
    if code == 401:
        return ("API key rejected (invalid_api_key)", "Paste a fresh key in .env without quotes, then restart make dev")
    if code in (400, 404, 422):
        return (f"voice or model not accepted (HTTP {code}: {message or status})",
                "Check ELEVENLABS_VOICE_ID (the ID, not the name) and ELEVENLABS_MODEL_ID. Library voices must first be added to My Voices")
    if code == 429:
        return ("ElevenLabs rate limit / too many concurrent requests", "Wait a moment and retry; free plans allow only a few parallel requests")
    return (f"ElevenLabs error HTTP {code}: {status} {message}".strip(), "See the server terminal for details")
