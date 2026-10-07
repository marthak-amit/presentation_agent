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
