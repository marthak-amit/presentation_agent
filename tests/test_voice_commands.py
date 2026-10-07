from __future__ import annotations

import asyncio

import pytest

from backend.session import messages as M
from backend.session.commands import Command, parse_command

from .helpers import FakeClient, make_ready_deck


@pytest.mark.parametrize("text,expected", [
    ("next slide", Command("next")), ("Next slide, please", Command("next")), ("go to the next slide", Command("next")),
    ("skip", Command("next")), ("previous slide", Command("prev")), ("go back", Command("prev")),
    ("go to slide three", Command("goto", 3)), ("show me slide 5", Command("goto", 5)), ("slide 2", Command("goto", 2)),
    ("first slide", Command("first")), ("the final slide", Command("last")), ("start over", Command("restart")),
    ("repeat this slide", Command("repeat_slide")), ("pause", Command("pause")), ("continue", Command("resume")),
    ("resume presenting", Command("resume")), ("okay agent next", Command("next")), ("repeat that", Command("resume")),
])
def test_commands_are_understood(text, expected):
    assert parse_command(text, 5) == expected


@pytest.mark.parametrize("text", [
    "what is on the next slide", "how much is the growth plan", "go to slide 12", "tell me about slide 3 pricing",
    "can the next version pause", "", "stop",
])
def test_questions_and_out_of_range_are_not_commands(text):
    assert parse_command(text, 5) is None


@pytest.fixture
async def deck(svc):
    return await make_ready_deck(svc)


async def started(svc, deck_id) -> FakeClient:
    c = FakeClient(svc, play_s=0.3)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await asyncio.sleep(0.4)
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    return c


async def test_next_slide_by_voice(svc, deck):
    c = await started(svc, deck)
    await c.say("next slide")
    vc = await c.wait_for(lambda: c.of("voice_command") and c.of("voice_command")[-1])
    assert vc["kind"] == "next" and vc["slide_n"] == 2
    await c.wait_state("PRESENTING")
    first = await c.wait_for(lambda: [m for m in c.of("play_sentence") if m["slide_n"] == 2])
    assert first[0]["sentence_i"] == 0
    assert "ack" in [m["clip"] for m in c.of("play_clip")] and not c.of("model_info")  # no LLM call for a command
    await c.close()


async def test_go_to_slide_four_and_single_word_commands(svc, deck):
    c = await started(svc, deck)
    await c.say("go to slide four")
    await c.wait_for(lambda: [m for m in c.of("play_sentence") if m["slide_n"] == 4])
    await c.session.handle(M.HandRaise())
    await c.wait_state("LISTENING")
    await c.say("previous")  # single word is fine when it is a command
    await c.wait_for(lambda: [m for m in c.of("play_sentence") if m["slide_n"] == 3])
    await c.close()


async def test_a_question_that_mentions_slides_still_goes_to_the_llm(svc, deck):
    c = await started(svc, deck)
    await c.say("what is on the next slide about pricing")
    await c.wait_for(lambda: c.of("model_info"))
    assert not c.of("voice_command")
    await c.close()


async def test_pause_then_wake_phrase_continue(svc, deck):
    c = await started(svc, deck)
    await c.say("pause")
    await c.wait_state("PAUSED")
    assert c.of("pause")[-1]["reason"] == "voice"
    n = len(c.of("play_sentence"))
    await asyncio.sleep(0.6)
    assert len(c.of("play_sentence")) == n  # really paused
    await c.sim("okay agent", final=False)  # wake phrase works while paused
    await c.wait_state("LISTENING")
    await c.sim("okay agent", final=True)
    await c.sim("continue", final=True, end=True)
    await c.wait_for(lambda: "continue" in [m["clip"] for m in c.of("play_clip")])
    await c.wait_state("PRESENTING")
    await c.close()


async def test_restart_after_the_deck_ended(svc, deck):
    c = FakeClient(svc, play_s=0.005)
    await c.open(deck)
    await c.ctl("start")
    await c.wait_state("OPEN_QA", timeout=20)
    await asyncio.sleep(0.1)
    await c.say("go to slide two")
    await c.wait_state("PRESENTING")
    await c.wait_for(lambda: c.of("play_sentence")[-1]["slide_n"] >= 2 and len(c.of("play_sentence")) > 20)
    await c.close()
