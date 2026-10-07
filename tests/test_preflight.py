from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from backend.api import preflight as pf


_REAL = httpx.AsyncClient


def _patch_http(monkeypatch, handler):
    real = _REAL

    class Client(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(pf.httpx, "AsyncClient", Client)


async def test_groq_check_detects_bad_key_and_missing_models(svc, monkeypatch):
    svc.settings = replace(svc.settings, groq_api_key="k", groq_qa_model="m-qa", groq_script_model="m-qa",
                           groq_fallback_model="m-typo")
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "m-qa"}]}))
    c = await pf.check_groq(svc)
    assert c.status == "fail" and "m-typo" in c.detail and "GROQ_" in c.fix

    _patch_http(monkeypatch, lambda req: httpx.Response(401, json={}))
    assert (await pf.check_groq(svc)).status == "fail"

    svc.settings = replace(svc.settings, groq_fallback_model="m-qa")
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "m-qa"}]}))
    c = await pf.check_groq(svc)
    assert c.status == "ok" and c.ms is not None


async def test_elevenlabs_check_voice_id_and_key(svc, monkeypatch):
    svc.settings = replace(svc.settings, elevenlabs_api_key="k", elevenlabs_voice_id="vid", elevenlabs_model_id="mm")
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"name": "Amit clone", "category": "cloned"}))
    c = await pf.check_tts(svc)
    assert c.status == "ok" and "Amit clone" in c.detail
    _patch_http(monkeypatch, lambda req: httpx.Response(404, json={}))
    c = await pf.check_tts(svc)
    assert c.status == "fail" and "voice id" in c.detail.lower()
    _patch_http(monkeypatch, lambda req: httpx.Response(401, json={}))
    assert (await pf.check_tts(svc)).status == "fail"


async def test_mock_services_are_reported_as_mock_not_failures(svc):
    assert (await pf.check_groq(svc)).status == "mock"
    assert (await pf.check_deepgram(svc))[0].status == "mock"
    assert (await pf.check_tts(svc)).status == "mock"
    assert (await pf.check_storage(svc)).status == "ok"


def test_preflight_endpoint_and_voice_sample(svc):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app(svc)) as c:
        j = c.get("/preflight").json()
        assert j["overall"] in ("ok", "warn", "fail") and {x["id"] for x in j["checks"]} >= {"groq", "deepgram", "tts", "storage"}
        v = c.get("/preflight/voice")
        assert v.status_code == 200 and v.headers["content-type"] == "audio/mpeg" and len(v.content) > 200


async def test_groq_check_lists_models_the_key_does_have(svc, monkeypatch):
    svc.settings = replace(svc.settings, groq_api_key="k", groq_qa_model="openai/gpt-oss-120b",
                           groq_script_model="openai/gpt-oss-120b", groq_fallback_model="llama-3.1-8b-instant")
    ids = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "whisper-large-v3"]
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": i} for i in ids]}))
    c = await pf.check_groq(svc)
    assert c.status == "fail" and "llama-3.1-8b-instant" in c.detail
    assert "openai/gpt-oss-20b" in c.fix and "llama-3.3-70b-versatile" in c.fix and "whisper" not in c.fix


async def test_elevenlabs_restricted_key_is_verified_by_real_synthesis(svc, monkeypatch):
    svc.settings = replace(svc.settings, elevenlabs_api_key="k", elevenlabs_voice_id="vid", elevenlabs_model_id="mm")
    perm = {"detail": {"status": "missing_permissions", "message": "needs voices_read"}}

    def h(req):
        if req.method == "GET":
            return httpx.Response(401, json=perm)
        return httpx.Response(200, content=b"\xff\xfb\x90\x00" + bytes(500))

    _patch_http(monkeypatch, h)
    c = await pf.check_tts(svc)
    assert c.status == "ok" and "Voices: read" in c.detail  # works for TTS even without voices_read

    def h2(req):
        return httpx.Response(401, json=perm)

    _patch_http(monkeypatch, h2)
    c = await pf.check_tts(svc)
    assert c.status == "fail" and "permission" in c.detail and "Text to Speech" in c.fix

    _patch_http(monkeypatch, lambda req: httpx.Response(401, json={"detail": {"status": "invalid_api_key"}}))
    c = await pf.check_tts(svc)
    assert c.status == "fail" and "invalid_api_key" in c.detail and "RESTART" in c.fix


async def test_env_edited_after_start_is_flagged(svc, tmp_path, monkeypatch):
    import os

    import backend.config as cfg

    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("A=1")
    svc.started_at = os.path.getmtime(tmp_path / ".env") - 100
    c = await pf.check_env_fresh(svc)
    assert c.status == "warn" and "RESTART" not in c.fix and "again" in c.fix
    svc.started_at = os.path.getmtime(tmp_path / ".env") + 100
    assert (await pf.check_env_fresh(svc)).status == "ok"
