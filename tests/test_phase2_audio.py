from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.tts.cache import AudioCache
from backend.tts.chain import FallbackTTS
from backend.tts.clips import STOCK_CLIPS, clip_text
from backend.tts.fake import FakeTTS, estimate_seconds, silent_mp3

from .conftest import SAMPLE
from .helpers import FakeClient, make_ready_deck


# ---------------------------------------------------------------- TTS layer
@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe missing")
def test_fake_mp3_is_valid_and_sized(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(silent_mp3(2.0))
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    assert 1.9 < float(out) < 2.1
    assert estimate_seconds("one two three four five six") > 0.7


async def test_cache_never_regenerates_real_audio(tmp_path):
    calls = []

    class Real:
        name = "elevenlabs"

        async def synth(self, text):
            calls.append(text)
            return b"\xff\xfb\x90\x00" + bytes(500)

    cache = AudioCache(FallbackTTS([Real(), FakeTTS()]))
    path = tmp_path / "audio" / "1" / "s0.mp3"
    await cache.ensure(path, "hello there")
    mtime = path.stat().st_mtime_ns
    await cache.ensure(path, "hello there")
    assert len(calls) == 1 and path.stat().st_mtime_ns == mtime
    await cache.ensure(path, "different text")  # text changed -> regenerate
    assert len(calls) == 2


async def test_cache_upgrades_mock_audio_when_real_provider_appears(tmp_path):
    path = tmp_path / "s0.mp3"
    await AudioCache(FallbackTTS([FakeTTS()])).ensure(path, "hi")
    calls = []

    class Real:
        name = "aura"

        async def synth(self, text):
            calls.append(1)
            return bytes(600)

    await AudioCache(FallbackTTS([Real(), FakeTTS()])).ensure(path, "hi")
    assert calls == [1]


async def test_tts_chain_falls_back_and_trips_breaker():
    class Bad:
        name = "elevenlabs"
        n = 0

        async def synth(self, text):
            Bad.n += 1
            raise RuntimeError("503")

    now = [0.0]
    chain = FallbackTTS([Bad(), FakeTTS()], cooldown_s=30, clock=lambda: now[0])
    audio, provider = await chain.synth("hello world")
    assert provider == "fake" and len(audio) > 100
    await chain.synth("again")
    assert Bad.n == 1  # breaker open: no second call
    now[0] = 31
    await chain.synth("later")
    assert Bad.n == 2


# ---------------------------------------------------------------- pre-generation
async def test_pregen_creates_sentence_audio_and_stock_clips(svc):
    deck_id = await make_ready_deck(svc)
    narr = svc.store.narration(deck_id)
    for s in narr:
        for i, _ in enumerate(s["sentences"]):
            assert svc.store.audio_path(deck_id, s["n"], i).stat().st_size > 100
    meta = svc.store.meta(deck_id)
    assert meta["audio"]["done"] == meta["audio"]["total"] > 10
    for key in STOCK_CLIPS:
        assert (svc.settings.stock_dir / f"{key}.mp3").exists()
    assert clip_text("followup", "Amit") == "Great question — I'll have Amit follow up on that."
    assert clip_text("go_ahead", "Amit") == "Sure, go ahead."


# ---------------------------------------------------------------- presenting
async def test_presents_whole_deck_in_order_then_open_qa(svc):
    deck_id = await make_ready_deck(svc)
    c = FakeClient(svc)
    await c.open(deck_id)
    assert c.of("session_ready")[0]["slide_count"] == 5
    await c.ctl("start")
    await c.wait_state("OPEN_QA", timeout=20)
    plays = c.of("play_sentence")
    expected = [(s["n"], i) for s in svc.store.narration(deck_id) for i, _ in enumerate(s["sentences"])]
    assert [(p["slide_n"], p["sentence_i"]) for p in plays] == expected  # playhead order, no repeats
    assert c.states()[:2] == ["IDLE", "PRESENTING"] and "END" in c.states()
    assert c.of("play_clip")[-1]["clip"] == "open_qa"
    assert {p["slide_n"] for p in plays} == {1, 2, 3, 4, 5}  # slides auto-advance via play_sentence.slide_n
    await c.close()


async def test_pause_resume_restarts_sentence_and_next_prev(svc):
    deck_id = await make_ready_deck(svc)
    c = FakeClient(svc, auto_ack=False)
    await c.open(deck_id)
    await c.ctl("start")
    first = await c.wait_for(lambda: c.of("play_sentence"), desc="first sentence")
    assert (first[0]["slide_n"], first[0]["sentence_i"]) == (1, 0)
    await c.ack_now(first[0]["play_id"])
    second = await c.wait_for(lambda: len(c.of("play_sentence")) >= 2 and c.of("play_sentence")[1])
    assert second["sentence_i"] == 1
    await c.ctl("pause")
    assert c.session.state.value == "PAUSED" and c.of("pause")[-1]["fade_ms"] == 150
    n_before = len(c.of("play_sentence"))
    await asyncio.sleep(0.15)
    assert len(c.of("play_sentence")) == n_before  # nothing plays while paused
    await c.ctl("resume")
    again = await c.wait_for(lambda: len(c.of("play_sentence")) > n_before and c.of("play_sentence")[-1])
    assert (again["slide_n"], again["sentence_i"]) == (1, 1)  # restarted at START of interrupted sentence
    await c.ctl("next")
    nxt = await c.wait_for(lambda: c.of("play_sentence")[-1]["slide_n"] == 2 and c.of("play_sentence")[-1])
    assert nxt["sentence_i"] == 0
    await c.ctl("prev")
    back = await c.wait_for(lambda: c.of("play_sentence")[-1]["slide_n"] == 1 and c.of("play_sentence")[-1])
    assert back["sentence_i"] == 0
    # stale ack for an old play must be harmless
    await c.ack_now("nonexistent")
    await c.close()


async def test_sentence_audio_generated_on_demand_when_missing(svc):
    deck_id = await make_ready_deck(svc, audio=False)
    shutil.rmtree(svc.store.dir(deck_id) / "audio", ignore_errors=True)  # a global post-ingest hook may have run
    assert not svc.store.audio_path(deck_id, 1, 0).exists()
    c = FakeClient(svc)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    assert svc.store.audio_path(deck_id, 1, 0).exists()
    await c.close()


# ---------------------------------------------------------------- WS wiring
def test_websocket_end_to_end(svc):
    app = create_app(svc)
    with TestClient(app) as http:
        with open(SAMPLE, "rb") as f:
            deck_id = http.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")}).json()["deck_id"]
        import time

        for _ in range(300):
            d = http.get(f"/decks/{deck_id}").json()
            if d["status"] == "ready" and d["audio"]["total"] and d["audio"]["done"] == d["audio"]["total"]:
                break
            time.sleep(0.1)
        assert d["status"] == "ready" and d["audio"]["done"] == d["audio"]["total"], d
        assert d["slides"][0]["audio_urls"][0].endswith("/audio/1/s0.mp3")
        with http.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "bogus"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "control", "action": "start"})
            assert "start_session" in ws.receive_json()["message"]
            ws.send_json({"type": "start_session", "deck_id": deck_id})
            assert ws.receive_json()["type"] == "session_ready"
            ws.send_json({"type": "control", "action": "start"})
            seen = []
            while True:
                m = ws.receive_json()
                seen.append(m["type"])
                if m["type"] == "play_sentence":
                    assert http.get(m["url"]).status_code == 200  # audio is actually served
                    ws.send_json({"type": "audio_ended", "play_id": m["play_id"]})
                    break
            assert "state" in seen and "slide" in seen
        assert http.get("/stock/go_ahead.mp3").status_code == 200
