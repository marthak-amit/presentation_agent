"""Showtime features: typed questions, sources, suggestions, dress rehearsal."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.session import messages as M

from .conftest import SAMPLE
from .helpers import FakeClient, make_ready_deck


@pytest.fixture
async def deck(svc):
    return await make_ready_deck(svc)


async def presenting(svc, deck_id, play_s=0.3):
    c = FakeClient(svc, play_s=play_s)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await asyncio.sleep(0.4)
    return c


async def test_typed_question_pauses_and_is_answered_without_a_microphone(svc, deck):
    c = await presenting(svc, deck)
    interrupted = c.of("play_sentence")[-1]
    await c.session.handle(M.Ask(text="How much is the Growth plan per month?"))
    hit = await c.wait_for(lambda: c.of("barge_in_hit") and c.of("barge_in_hit")[0])
    assert hit["source"] == "typed" and c.of("pause")[0]["reason"] == "ask"
    assert "go_ahead" not in [m["clip"] for m in c.of("play_clip")]  # the question is already there
    info = await c.wait_for(lambda: c.of("model_info") and c.of("model_info")[-1])
    assert info["question"] == "How much is the Growth plan per month?" and c.of("play_answer")
    await c.wait_state("PRESENTING", timeout=15)  # 'anything else?' -> silence -> continue
    resumed = await c.wait_for(lambda: c.of("play_sentence")[-1]["play_id"] != interrupted["play_id"] and c.of("play_sentence")[-1])
    assert (resumed["slide_n"], resumed["sentence_i"]) == (interrupted["slide_n"], interrupted["sentence_i"])
    await c.close()


async def test_typed_question_while_listening_and_guards(svc, deck):
    c = FakeClient(svc, play_s=0.3)
    await c.open(deck)
    await c.session.handle(M.Ask(text="before we started?"))  # IDLE
    assert "Start the presentation" in c.of("error")[-1]["message"]
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await c.session.handle(M.Ask(text="ok"))  # too short: ignored
    await asyncio.sleep(0.2)
    assert c.session.state.value == "PRESENTING"
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.session.handle(M.Ask(text="when does the mobile app ship"))
    await c.wait_for(lambda: c.of("model_info"))
    await c.close()


async def test_answer_start_names_the_sources(svc, deck):
    c = await presenting(svc, deck)
    await c.session.handle(M.Ask(text="what are the starter growth and scale pricing tiers in dollars"))
    msg = await c.wait_for(lambda: c.of("answer_start") and c.of("answer_start")[0])
    assert msg["question"].startswith("what are the starter")
    kinds = {(s["kind"], s["slide_n"]) for s in msg["sources"]}
    assert ("slide", 5) in kinds and all(s["kind"] in ("slide", "doc") for s in msg["sources"])
    await c.close()


def _ready(http):
    with open(SAMPLE, "rb") as f:
        deck_id = http.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")}).json()["deck_id"]
    for _ in range(300):
        d = http.get(f"/decks/{deck_id}").json()
        if d["status"] == "ready":
            return deck_id
        time.sleep(0.1)
    raise AssertionError(d)


def test_suggestions_are_cached_and_validated(svc):
    with TestClient(create_app(svc)) as http:
        deck_id = _ready(http)
        qs = http.get(f"/decks/{deck_id}/suggestions").json()["questions"]
        assert 3 <= len(qs) <= 6 and all(q.endswith("?") for q in qs)
        assert (svc.store.dir(deck_id) / "suggestions.json").exists()
        assert http.get(f"/decks/{deck_id}/suggestions").json()["questions"] == qs
        assert http.get("/decks/aaaaaaaaaaaa/suggestions").status_code == 404


async def test_real_llm_suggestions_are_parsed(svc):
    from backend.ingest.suggest import clean_questions, suggest_questions

    assert clean_questions("1. How much is Growth?\n- Why now?\nShort\nWhat ships in Q4? \n\"Is it secure for retail teams?\"") == [
        "How much is Growth?", "What ships in Q4?", "Is it secure for retail teams?"]

    class Llm:
        name = "groq"

        async def complete(self, **kw):
            return "Who is the pilot customer base?\nHow does the dashboard stay up to date?\nWhat does Growth cost per month?\nWhy now?"

    deck_id = await make_ready_deck(svc, audio=False)
    svc.llm = Llm()
    qs = await suggest_questions(svc, deck_id)
    assert qs[0] == "Who is the pilot customer base?" and len(qs) == 3  # "Why now?" is too short to be a good chip


def test_dress_rehearsal_reports_timings(svc):
    with TestClient(create_app(svc)) as http:
        assert http.post("/selftest").status_code == 400  # no deck yet
        deck_id = _ready(http)
        r = http.post("/selftest", params={"deck_id": deck_id})
        assert r.status_code == 200
        j = r.json()
        for k in ("question", "answer", "model", "retrieval_ms", "first_sentence_ms", "tts_ms", "first_audio_ms", "verdict", "tts_provider"):
            assert k in j, k
        assert j["answer"] and j["first_audio_ms"] >= j["tts_ms"] and any("Mock" in n for n in j["notes"])


def test_dress_rehearsal_flags_a_dead_llm(svc):
    class Down:
        name = "groq"

        async def complete(self, **kw):
            raise RuntimeError("down")

        async def stream(self, **kw):
            raise RuntimeError("network unreachable")
            yield  # pragma: no cover

    with TestClient(create_app(svc)) as http:
        deck_id = _ready(http)
        svc.llm = Down()
        j = http.post("/selftest", params={"deck_id": deck_id}).json()
        assert j["ok"] is False and j["verdict"] == "fail" and "canned" in j["error"]
