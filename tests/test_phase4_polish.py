from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services import attach_tts
from backend.session import messages as M
from backend.tts.chain import FallbackTTS
from backend.tts.fake import FakeTTS

from .conftest import SAMPLE
from .helpers import FakeClient, make_ready_deck


class CountingProvider:
    def __init__(self, name="elevenlabs", fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    async def synth(self, text):
        self.calls += 1
        if self.fail:
            raise ConnectionError("network unreachable")
        return b"\xff\xfb\x90\x00" + bytes(2000)


async def test_offline_during_presenting_keeps_playing_cached_audio(svc):
    ok = CountingProvider()
    attach_tts(svc, FallbackTTS([ok, FakeTTS(speed=25)]))
    deck_id = await make_ready_deck(svc)  # real-provider audio is now cached on disk
    assert ok.calls > 10

    # the network dies: every provider call would now fail, the LLM and STT are gone too
    dead = CountingProvider(fail=True)
    attach_tts(svc, FallbackTTS([dead, FakeTTS(speed=25)]))

    class DeadSTT:
        name, status = "deepgram", "down"

        async def start(self): ...
        async def send_audio(self, d): ...
        async def finalize(self): ...
        async def close(self): ...

    c = FakeClient(svc, play_s=0.005)
    await c.open(deck_id)
    c.session.stt = DeadSTT()
    await c.ctl("start")
    await c.wait_state("OPEN_QA", timeout=20)
    plays = c.of("play_sentence")
    assert len(plays) == sum(len(s["sentences"]) for s in svc.store.narration(deck_id))  # whole deck played
    assert dead.calls == 0  # cached audio only - no network needed while presenting
    st = [m for m in c.of("state") if m["warnings"]]
    assert st and "offline" in st[-1]["warnings"][0].lower()
    await c.close()


async def test_missing_audio_while_tts_is_down_degrades_to_silent_and_continues(svc):
    deck_id = await make_ready_deck(svc, audio=False)
    import shutil

    shutil.rmtree(svc.store.dir(deck_id) / "audio", ignore_errors=True)
    dead = CountingProvider(fail=True)
    attach_tts(svc, FallbackTTS([dead, FakeTTS(speed=25)]))
    c = FakeClient(svc, play_s=0.005)
    await c.open(deck_id)
    t0 = time.monotonic()
    await c.ctl("start")
    await c.wait_state("OPEN_QA", timeout=30)
    assert dead.calls == 1  # circuit breaker: one failure, then straight to the fallback
    assert time.monotonic() - t0 < 25
    await c.close()


async def test_session_log_records_every_interruption(svc):
    deck_id = await make_ready_deck(svc)
    c = FakeClient(svc, play_s=0.2)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await asyncio.sleep(0.4)
    await c.sim("excuse me", final=False)
    await c.wait_state("LISTENING")
    await c.say("what is the price of the growth plan")
    await c.wait_state("PRESENTING", timeout=10)
    rows = svc.logs.read_session(c.session.session_id)
    assert len(rows) == 1
    row = rows[0]
    for key in ("ts", "trigger", "question", "answer", "model", "first_token_ms", "first_audio_ms", "total_ms"):
        assert key in row and row[key] not in (None, ""), key
    assert row["trigger"] == "excuse me" and row["question"] == "what is the price of the growth plan"
    assert c.of("summary")[-1]["questions"][0]["question"] == row["question"]
    await c.close()


def test_debug_ws_streams_events_and_routes_simulated_speech(svc):
    app = create_app(svc)
    with TestClient(app) as http:
        with open(SAMPLE, "rb") as f:
            deck_id = http.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")}).json()["deck_id"]
        for _ in range(300):
            d = http.get(f"/decks/{deck_id}").json()
            if d["status"] == "ready" and d["audio"]["total"] and d["audio"]["done"] == d["audio"]["total"]:
                break
            time.sleep(0.1)
        health = http.get("/health").json()
        assert health["ok"] and health["services"]["llm"] == "fake"
        with http.websocket_connect("/ws/debug") as dbg, http.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "start_session", "deck_id": deck_id})
            ready = ws.receive_json()
            sid = ready["session_id"]
            ws.send_json({"type": "control", "action": "start"})
            ws.send_json({"type": "hand_raise"})
            seen = {}
            deadline = time.time() + 15
            while time.time() < deadline and "LISTENING" not in seen:
                m = ws.receive_json()
                if m["type"] == "play_sentence" or m["type"] == "play_clip":
                    ws.send_json({"type": "audio_ended", "play_id": m["play_id"]})
                if m["type"] == "state":
                    seen[m["state"]] = True
            assert "LISTENING" in seen
            dbg.send_json({"type": "sim_transcript", "session_id": sid, "text": "what does the product connect to",
                           "is_final": True, "utterance_end": True})
            got_model, kinds = None, set()
            deadline = time.time() + 15
            while time.time() < deadline and got_model is None:
                m = ws.receive_json()
                if m["type"] in ("play_clip", "play_answer"):
                    ws.send_json({"type": "audio_ended", "play_id": m["play_id"]})
                if m["type"] == "model_info":
                    got_model = m
            assert got_model and got_model["total_ms"] > 0
            # the debug socket saw the same session's events (state, transcript, model_info)
            deadline = time.time() + 5
            while time.time() < deadline and not {"state", "transcript", "model_info"} <= kinds:
                ev = dbg.receive_json()
                assert ev["session_id"] == sid
                kinds.add(ev["event"]["type"])
            assert {"state", "transcript", "model_info"} <= kinds
        # REST views of the logs
        sessions = http.get("/sessions").json()
        assert any(s["session_id"] == sid and s["interruptions"] == 1 for s in sessions)
        assert http.get(f"/sessions/{sid}/log").json()[0]["question"] == "what does the product connect to"
        assert isinstance(http.get("/logs/unanswered").json(), list)
