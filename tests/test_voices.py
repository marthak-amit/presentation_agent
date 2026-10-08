from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import voices as vo
from backend.envfile import set_env_var
from backend.main import create_app
from backend.services import attach_tts

from .conftest import SAMPLE

_REAL = httpx.AsyncClient
AUDIO = b"\xff\xfb\x90\x00" + bytes(600)


def patch_http(monkeypatch, handler):
    class Client(_REAL):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(vo.httpx, "AsyncClient", Client)


def elevenlabs(calls=None, library_ids=()):
    """Fake ElevenLabs: list voices + TTS (library voices -> 402 like a free plan)."""

    def h(req: httpx.Request):
        if calls is not None:
            calls.append((req.method, req.url.path))
        if req.method == "GET" and req.url.path == "/v1/voices":
            return httpx.Response(200, json={"voices": [
                {"voice_id": "premade00000000000001", "name": "Rachel", "category": "premade", "labels": {"accent": "american"}},
                {"voice_id": "cloned000000000000002", "name": "Amit clone", "category": "cloned", "labels": {}},
                {"voice_id": "library0000000000003", "name": "Amay - Professional Indian English", "category": "professional",
                 "labels": {"accent": "indian"}},
            ]})
        if req.method == "POST" and "/text-to-speech/" in req.url.path:
            vid = req.url.path.rsplit("/", 1)[-1]
            if vid in library_ids:
                return httpx.Response(402, json={"detail": {"status": "paid_plan_required",
                                                            "message": "Free users cannot use library voices via the API."}})
            return httpx.Response(200, content=AUDIO)
        return httpx.Response(404, json={})

    return h


@pytest.fixture
def app_svc(svc, tmp_path, monkeypatch):
    svc.settings = replace(svc.settings, elevenlabs_api_key="k", elevenlabs_voice_id="library0000000000003",
                           elevenlabs_model_id="m")
    attach_tts(svc)
    monkeypatch.setattr(vo, "ROOT", tmp_path)  # never touch the real .env
    return svc


def test_set_env_var_updates_in_place_and_creates(tmp_path):
    p = tmp_path / ".env"
    set_env_var(p, "ELEVENLABS_VOICE_ID", "abc1234567")
    assert p.read_text() == "ELEVENLABS_VOICE_ID=abc1234567\n"
    p.write_text("# keys\nGROQ_API_KEY=gsk_x\nELEVENLABS_VOICE_ID=old\nPRESENTER_NAME=Bytes Technolab developer\nELEVENLABS_VOICE_ID=dup\n")
    set_env_var(p, "ELEVENLABS_VOICE_ID", "newid12345")
    assert p.read_text() == "# keys\nGROQ_API_KEY=gsk_x\nELEVENLABS_VOICE_ID=newid12345\nPRESENTER_NAME=Bytes Technolab developer\n"
    with pytest.raises(ValueError):
        set_env_var(p, "bad key", "x")
    with pytest.raises(ValueError):
        set_env_var(p, "OK", "line1\nINJECT=1")


def test_list_marks_current_and_orders_own_voices_first(app_svc, monkeypatch):
    patch_http(monkeypatch, elevenlabs())
    with TestClient(create_app(app_svc)) as c:
        j = c.get("/voices").json()
    assert j["current"] == "library0000000000003"
    names = [v["name"] for v in j["voices"]]
    assert names[0].startswith("Amay") and j["voices"][0]["current"] is True  # current first
    assert names.index("Amit clone") < names.index("Rachel")


def test_test_endpoint_plays_good_voices_and_explains_bad_ones(app_svc, monkeypatch):
    patch_http(monkeypatch, elevenlabs(library_ids=("library0000000000003",)))
    with TestClient(create_app(app_svc)) as c:
        ok = c.post("/voices/test", json={"voice_id": "premade00000000000001"})
        assert ok.status_code == 200 and ok.headers["content-type"] == "audio/mpeg" and len(ok.content) > 200
        bad = c.post("/voices/test", json={"voice_id": "library0000000000003"})
        assert bad.status_code == 422
        d = bad.json()["detail"]
        assert "Voice Library" in d["what"] and "YOUR account" in d["fix"]
        assert c.post("/voices/test", json={"voice_id": "not an id!"}).status_code == 400


def test_select_switches_live_saves_env_and_revoices(app_svc, tmp_path, monkeypatch):
    calls = []
    patch_http(monkeypatch, elevenlabs(calls, library_ids=("library0000000000003",)))
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=k\nELEVENLABS_VOICE_ID=library0000000000003\n")
    with TestClient(create_app(app_svc)) as c:
        with open(SAMPLE, "rb") as f:
            deck_id = c.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")}).json()["deck_id"]
        for _ in range(300):
            if c.get(f"/decks/{deck_id}").json()["status"] == "ready":
                break
            time.sleep(0.1)
        r = c.post("/voices/select", json={"voice_id": "premade00000000000001"})
        assert r.status_code == 200 and r.json()["revoicing_decks"] == 1
        assert "ELEVENLABS_VOICE_ID=premade00000000000001" in (tmp_path / ".env").read_text()
        assert app_svc.settings.elevenlabs_voice_id == "premade00000000000001"
        assert app_svc.tts.voice_of("elevenlabs").startswith("premade00000000000001:")  # live chain rebuilt
        # the deck gets re-spoken by the new voice in the background
        used = set()
        for _ in range(200):
            used = {p.rsplit("/", 1)[-1] for m, p in calls if m == "POST" and "/text-to-speech/" in p}
            d = c.get(f"/decks/{deck_id}").json()
            if d["audio"]["total"] and d["audio"].get("providers", {}).get("elevenlabs", 0) == d["audio"]["total"]:
                break
            time.sleep(0.1)
        assert "premade00000000000001" in used


def test_select_refuses_a_voice_that_cannot_speak_and_keeps_the_old_one(app_svc, tmp_path, monkeypatch):
    patch_http(monkeypatch, elevenlabs(library_ids=("library0000000000003",)))
    (tmp_path / ".env").write_text("ELEVENLABS_VOICE_ID=premade00000000000001\n")
    app_svc.settings = replace(app_svc.settings, elevenlabs_voice_id="premade00000000000001")
    with TestClient(create_app(app_svc)) as c:
        r = c.post("/voices/select", json={"voice_id": "library0000000000003"})
    assert r.status_code == 422 and "Voice Library" in r.json()["detail"]["what"]
    assert (tmp_path / ".env").read_text() == "ELEVENLABS_VOICE_ID=premade00000000000001\n"
    assert app_svc.settings.elevenlabs_voice_id == "premade00000000000001"


def test_no_key_and_restricted_key_messages(svc, monkeypatch):
    with TestClient(create_app(svc)) as c:
        assert c.get("/voices").status_code == 400
    svc.settings = replace(svc.settings, elevenlabs_api_key="k")
    patch_http(monkeypatch, lambda req: httpx.Response(401, json={"detail": {"status": "missing_permissions", "message": "needs voices_read"}}))
    with TestClient(create_app(svc)) as c:
        r = c.get("/voices")
    assert r.status_code == 502 and "Voices: read" in r.json()["detail"]["what"] and "manually" in r.json()["detail"]["fix"]


def test_cors_only_allows_the_local_frontend(svc):
    with TestClient(create_app(svc)) as c:
        ok = c.options("/voices/select", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        evil = c.options("/voices/select", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in evil.headers
