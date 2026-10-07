from __future__ import annotations

import time

import pytest

from backend.session.barge_in import (ALL_TRIGGERS, TRIGGERS, BargeInDetector, EchoGuard, UtteranceBuffer, find_trigger,
                                      is_dismissal, normalize, similarity, strip_leading_trigger)
from backend.session.sentences_stream import SentenceStreamer


@pytest.mark.parametrize("text", [
    "I have a question", "Quick question!", "Can I ask something", "Excuse me", "wait", "Hold on a second",
    "Sorry to interrupt", "one question", "ek sawaal", "ek minute", "ruko", "mera question hai", "ek prashn", "ubho raho",
    "i have a questin",  # STT typo
    "um, I have a question about pricing", "एक सवाल है", "WAIT!",
])
def test_every_trigger_family_fires(text):
    assert find_trigger(text) is not None, text


@pytest.mark.parametrize("text", [
    "we are waiting for the results", "the weight of this", "so I have", "I have a plan for the quarter",
    "that's a great quest", "the next slide shows revenue", "",
])
def test_non_triggers_do_not_fire(text):
    assert find_trigger(text) is None, text


def test_all_spec_triggers_present():
    spec = ["i have a question", "quick question", "can i ask", "excuse me", "wait", "hold on", "sorry to interrupt",
            "one question", "ek sawaal", "ek minute", "ruko", "mera question", "ek prashn", "ubho raho"]
    assert TRIGGERS == spec and set(spec) <= set(ALL_TRIGGERS)


def test_words_after_trigger_and_strip():
    m = find_trigger("Hey I have a question about the enterprise pricing tier")
    assert m.trigger == "i have a question" and m.words_after == 5
    assert strip_leading_trigger("Hey I have a question about the enterprise pricing tier") == "about the enterprise pricing tier"
    assert strip_leading_trigger("I have a question") == ""
    assert strip_leading_trigger("what is the price") == "what is the price"


def test_confidence_cooldown_and_inline_question():
    now = [100.0]
    det = BargeInDetector(clock=lambda: now[0], cooldown_s=3.0)
    det.note_resume()
    assert det.check("I have a question", 0.95) is None  # inside 3 s cooldown
    now[0] += 3.1
    assert det.check("I have a question", 0.69) is None  # confidence < 0.7
    d = det.check("I have a question", 0.7)
    assert d is not None and d.treat_as_question is False
    d2 = det.check("I have a question about the Growth tier pricing", 0.9)
    assert d2.treat_as_question is True  # > 4 words after the trigger -> skip "go ahead"
    assert det.check("I have a question about the Growth", 0.9).treat_as_question is False  # exactly 4 words after


def test_echo_guard_ignores_agents_own_voice():
    narration = "So what happens next is that we pause and say I have a question for the audience."
    det = BargeInDetector(clock=lambda: 1000.0, cooldown_s=0)
    det.echo.speaking(narration)
    assert det.check("we pause and say I have a question for the audience", 0.95) is None  # echo
    # a phrase the narration itself contains is indistinguishable from echo and is (by design) ignored...
    assert det.check("I have a question", 0.95) is None
    # ...but any trigger the narration does not contain still fires
    assert det.check("excuse me", 0.95) is not None
    assert similarity("wait", "please wait for the results") == 0.0  # single words never count as echo


def test_echo_similarity_threshold_semantics():
    s = "Our fastest growing tier is the Growth plan and weekly usage is above ninety percent"
    assert similarity("fastest growing tier is the growth plan", s) > 0.6
    assert similarity("what is the price of the growth plan", s) < 0.6


def test_detection_is_fast():
    det = BargeInDetector(cooldown_s=0)
    det.echo.speaking("Small retailers lose about eight percent of their revenue to stockouts.")
    t = time.perf_counter()
    for _ in range(50):
        det.check("hey I have a question about pricing", 0.9)
    per_call_ms = (time.perf_counter() - t) * 1000 / 50
    assert per_call_ms < 20, per_call_ms  # budget is 400 ms end to end


def test_utterance_buffer_captures_from_trigger():
    b = UtteranceBuffer()
    b.add("ignored while not capturing", True)
    assert b.text() == ""
    interim = "so anyway I have a question"
    b.seed_from_trigger(interim, find_trigger(interim).start_char)
    assert b.text() == "I have a question"
    b.add("I have a question about", False)
    b.add("so anyway I have a question about pricing", True)  # first final: trimmed to the trigger
    b.add("for enterprise", True)
    assert b.text() == "I have a question about pricing for enterprise"
    b.reset()
    assert b.text() == "" and not b.capturing


def test_dismissals():
    assert is_dismissal("no thanks") and is_dismissal("that's all") and is_dismissal("Okay")
    assert not is_dismissal("what about churn") and not is_dismissal("no what is the churn rate")


def test_sentence_streamer_splits_incrementally():
    s = SentenceStreamer()
    out = []
    for tok in "The Growth plan is 149 dollars. It includes alerts! Dr. Lee agrees.".split(" "):
        out += s.feed(tok + " ")
    out += s.flush()
    assert out == ["The Growth plan is 149 dollars.", "It includes alerts!", "Dr. Lee agrees."]
    assert normalize("Hello,  World!") == "hello world"
