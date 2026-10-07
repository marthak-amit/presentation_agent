from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.session import messages as M

from .helpers import FakeClient, make_ready_deck


@pytest.fixture
async def deck(svc):
    return await make_ready_deck(svc)


async def presenting(svc, deck_id, play_s=0.3, barge_in=True) -> FakeClient:
    c = FakeClient(svc, play_s=play_s)
    await c.open(deck_id, barge_in=barge_in)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"), desc="first sentence")
    await asyncio.sleep(0.4)  # past the (shrunk) post-resume cooldown
    return c


def clips(c):
    return [m["clip"] for m in c.of("play_clip")]


async def test_voice_barge_in_full_flow(svc, deck):
    c = await presenting(svc, deck)
    interrupted = c.of("play_sentence")[-1]
    t0 = time.monotonic()
    await c.sim("I have a question", final=False)  # interim only: detection must not wait for the final
    detect_wall_ms = (time.monotonic() - t0) * 1000
    hit = c.of("barge_in_hit")[0]
    assert hit["trigger"] == "i have a question" and hit["detect_ms"] < 400 and detect_wall_ms < 400
    assert c.session.state.value in ("PAUSED", "LISTENING")
    pause_i = c.idx(lambda m: m["type"] == "pause")
    await c.wait_for(lambda: "go_ahead" in clips(c), desc="go-ahead clip")
    assert pause_i < c.idx(lambda m: m["type"] == "play_clip" and m["clip"] == "go_ahead")  # fade-out before "Sure, go ahead."
    assert c.of("pause")[0]["fade_ms"] == 150
    await c.wait_state("LISTENING")
    n_plays = len(c.of("play_sentence"))
    await asyncio.sleep(0.5)
    assert len(c.of("play_sentence")) == n_plays  # narration really stopped

    await c.sim("I have a question", final=True)
    await c.sim("how much is the Growth plan per month", final=True, end=True)
    await c.wait_for(lambda: c.of("model_info"), desc="answer")
    assert c.of("play_answer"), "answer audio must be streamed to the client"
    info = c.of("model_info")[-1]
    assert info["model"] == svc.settings.groq_qa_model and info["question"] == "how much is the Growth plan per month"
    assert info["first_token_ms"] is not None and info["first_audio_ms"] is not None and info["total_ms"] >= info["first_audio_ms"]
    assert "ANSWERING" in c.states()

    await c.wait_for(lambda: "anything_else" in clips(c), desc="anything else?")
    await c.wait_for(lambda: "continue" in clips(c), desc="continue clip (4s silence -> shrunk)")
    await c.wait_state("PRESENTING")
    resumed = await c.wait_for(lambda: c.of("play_sentence")[-1]["play_id"] != interrupted["play_id"] and c.of("play_sentence")[-1])
    assert (resumed["slide_n"], resumed["sentence_i"]) == (interrupted["slide_n"], interrupted["sentence_i"])  # START of interrupted sentence
    rec = svc.logs.read_session(c.session.session_id)
    assert len(rec) == 1 and rec[0]["trigger"] == "i have a question" and rec[0]["answer"] and rec[0]["total_ms"] > 0
    await c.close()


async def test_inline_question_skips_go_ahead(svc, deck):
    c = await presenting(svc, deck)
    await c.sim("excuse me I have a question what is the price of the growth plan", final=False)
    await c.wait_state("LISTENING")
    await c.sim("excuse me I have a question what is the price of the growth plan", final=True, end=True)
    await c.wait_for(lambda: c.of("model_info"))
    assert "go_ahead" not in clips(c)
    await c.close()


async def test_hand_raise_is_the_same_pause_event(svc, deck):
    c = await presenting(svc, deck)
    await c.session.handle(M.HandRaise())
    await c.wait_for(lambda: "go_ahead" in clips(c))
    assert c.of("pause") and c.of("barge_in_hit")[0]["source"] == "hand_raise"
    await c.wait_state("LISTENING")
    await c.say("what does the roadmap include")
    await c.wait_for(lambda: c.of("model_info"))
    await c.close()


async def test_barge_in_toggle_off_ignores_voice_but_hand_raise_works(svc, deck):
    c = await presenting(svc, deck, barge_in=False)
    await c.sim("I have a question", final=False)
    await asyncio.sleep(0.2)
    assert not c.of("pause") and c.session.state.value == "PRESENTING"
    await c.session.handle(M.SetBargeIn(enabled=True))
    await c.sim("I have a question", final=False)
    await c.wait_for(lambda: c.of("pause"))
    await c.close()


async def test_echo_and_cooldown_and_low_confidence_do_not_trigger(svc, deck):
    c = FakeClient(svc, play_s=0.3)
    await c.open(deck)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await c.sim("excuse me", final=False)  # inside the 3 s (shrunk: 0.3 s) cooldown after start
    assert not c.of("pause")
    await asyncio.sleep(0.4)
    await c.sim("excuse me", final=False, conf=0.5)
    assert not c.of("pause")
    current = c.of("play_sentence")[-1]["text"]
    await c.sim(current, final=False)  # the agent's own voice picked up by the mic
    assert not c.of("pause")
    await c.sim("excuse me", final=False)
    await c.wait_for(lambda: c.of("pause"))
    await c.close()


async def test_goto_slide_shows_slide_then_returns_to_origin(svc, deck):
    c = await presenting(svc, deck)
    origin = c.of("play_sentence")[-1]["slide_n"]
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("can you show me slide 5 and explain the pricing")
    await c.wait_for(lambda: any(m["temporary"] for m in c.of("slide")), desc="temporary slide")
    tmp = [m for m in c.of("slide") if m["temporary"]][0]
    assert tmp["slide_n"] == 5
    await c.wait_state("PRESENTING", timeout=10)
    back = await c.wait_for(lambda: [m for m in c.of("slide") if not m["temporary"]][-1])
    assert back["slide_n"] == origin  # returned to the slide we interrupted
    await c.close()


async def test_followup_question_loops_then_dismissal_continues(svc, deck):
    c = await presenting(svc, deck)
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("what is the price of the growth plan")
    await c.wait_for(lambda: "anything_else" in clips(c))
    await c.wait_state("LISTENING")
    await c.say("and what ships in the fourth quarter")  # follow-up inside the 4 s window
    await c.wait_for(lambda: len(c.of("model_info")) >= 2, desc="second answer")
    await c.wait_for(lambda: clips(c).count("anything_else") >= 2)
    await c.wait_state("LISTENING")
    await c.say("no thanks")  # dismissal -> continue immediately
    await c.wait_for(lambda: "continue" in clips(c))
    await c.wait_state("PRESENTING")
    assert len(svc.logs.read_session(c.session.session_id)) == 2
    await c.close()


async def test_unknown_question_is_logged_unanswered(svc, deck):
    c = await presenting(svc, deck)
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("explain your quantum blockchain strategy for mars")
    await c.wait_for(lambda: c.of("summary") and c.of("summary")[-1]["unanswered"])
    items = json.loads((svc.settings.logs_dir / "unanswered.json").read_text())
    assert items[-1]["question"].startswith("explain your quantum")
    await c.close()


async def test_all_llms_down_plays_canned_followup_clip(svc, deck):
    class Down:
        name = "down"

        async def complete(self, **kw):
            raise RuntimeError("down")

        async def stream(self, **kw):
            raise RuntimeError("network unreachable")
            yield  # pragma: no cover

    svc.llm = Down()
    c = await presenting(svc, deck)
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("what is the price")
    await c.wait_for(lambda: "followup" in clips(c), desc="canned follow-up clip")
    info = (await c.wait_for(lambda: c.of("model_info") and c.of("model_info")[-1]))
    assert info["model"] == "canned-clip"
    assert svc.logs.unanswered()[-1]["question"] == "what is the price"
    await c.wait_state("PRESENTING", timeout=10)  # the talk still continues
    await c.close()


async def test_slow_llm_plays_filler_and_falls_back(svc, deck):
    from dataclasses import replace

    from backend.llm.client import FakeLLM
    from backend.llm.types import Started

    class SlowPrimary(FakeLLM):
        async def stream(self, *, model, messages, tools=None, max_tokens=0, temperature=0):
            if model == svc.settings.groq_qa_model:
                await asyncio.sleep(10)
                yield Started()
            else:
                async for e in super().stream(model=model, messages=messages, tools=tools):
                    yield e

    svc.settings = svc.settings if False else svc.settings
    object.__setattr__(svc, "settings", replace(svc.settings, first_token_filler_s=0.1, first_token_timeout_s=0.4))
    svc.llm = SlowPrimary("Amit", delay=0)
    c = await presenting(svc, deck)
    c.session.cfg = svc.settings
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("what is the price of the growth plan")
    info = await c.wait_for(lambda: c.of("model_info") and c.of("model_info")[-1]["total_ms"] and c.of("model_info")[-1])
    assert "filler" in clips(c)
    assert info["model"] == svc.settings.groq_fallback_model and info["fallback_used"]
    await c.close()


async def test_push_to_talk(svc, deck):
    c = await presenting(svc, deck, barge_in=False)
    await c.session.handle(M.Ptt(active=True))
    await c.wait_state("LISTENING")
    assert "go_ahead" not in clips(c)
    await c.sim("what does the product connect to", final=True)  # no UtteranceEnd while the key is held
    await asyncio.sleep(0.1)
    assert not c.of("model_info")
    await c.session.handle(M.Ptt(active=False))
    await c.wait_for(lambda: c.of("model_info"), desc="answer after release")
    await c.close()


async def test_resume_button_skips_qa(svc, deck):
    c = await presenting(svc, deck)
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.ctl("resume")
    await c.wait_state("PRESENTING")
    await c.close()


async def test_open_qa_after_deck_end(svc, deck):
    c = FakeClient(svc, play_s=0.005)
    await c.open(deck)
    await c.ctl("start")
    await c.wait_state("OPEN_QA", timeout=20)
    await asyncio.sleep(0.1)
    await c.say("what is the price of the growth plan")
    await c.wait_for(lambda: c.of("model_info"))
    await c.wait_state("OPEN_QA")  # stays in Q&A, does not resume the talk
    await c.say("when does the mobile app ship")
    await c.wait_for(lambda: len(c.of("model_info")) >= 2)
    summ = c.of("summary")[-1]
    assert len(summ["questions"]) == 2
    await c.close()


async def test_hello_one_voice_command_stops_the_talk_and_listens(svc, deck):
    c = await presenting(svc, deck)
    await c.sim("Hello One", final=False)
    hit = await c.wait_for(lambda: c.of("barge_in_hit") and c.of("barge_in_hit")[0])
    assert hit["trigger"].startswith("hello ") and hit["detect_ms"] < 400
    pause = await c.wait_for(lambda: c.of("pause") and c.of("pause")[0])
    await c.wait_state("LISTENING")
    # presentation really stopped: nothing from the narration is sent after the pause
    n = len(c.of("play_sentence"))
    await asyncio.sleep(0.8)
    assert len(c.of("play_sentence")) == n and pause["fade_ms"] == 150
    await c.wait_for(lambda: "go_ahead" in clips(c))
    await c.sim("Hello One", final=True)
    await c.sim("how much is the growth plan per month", final=True, end=True)
    info = await c.wait_for(lambda: c.of("model_info") and c.of("model_info")[-1])
    assert info["question"] == "how much is the growth plan per month"  # wake phrase stripped from the question
    await c.close()
