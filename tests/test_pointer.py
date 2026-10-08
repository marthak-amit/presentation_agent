"""Animated pointer: line geometry, sentence->line matching, and the pointer riding on play_* messages."""
from __future__ import annotations

import asyncio
import shutil

import pytest

from backend.ingest.cursor import CursorMatcher, _num_words
from backend.ingest.layout import extract_layout, placeholder_layout
from backend.ingest.render import pptx_to_pdf
from backend.rag.embed import HashEmbedder

from .conftest import SAMPLE
from .helpers import FakeClient, make_ready_deck


@pytest.fixture(scope="module")
def layout(tmp_path_factory):
    if not shutil.which("soffice") or not shutil.which("pdftotext"):
        pytest.skip("LibreOffice/poppler missing")
    pdf = pptx_to_pdf(SAMPLE, tmp_path_factory.mktemp("pdf"))
    return extract_layout(pdf)


def test_number_words_match_digits():
    assert _num_words("forty-nine dollars") == "49 dollars"
    assert _num_words("one hundred forty-nine") == "149"
    assert _num_words("two thousand dollars") == "2000 dollars"
    assert _num_words("2,000 dollars") == "2000 dollars"


def test_layout_has_normalised_line_boxes(layout):
    assert set(layout) == {1, 2, 3, 4, 5}
    paras = layout[3]["paras"]
    assert [p["text"] for p in paras][:2] == ["The Product", "Live dashboard updated every 5 seconds"]
    for p in paras:
        for ln in p["lines"]:
            assert 0 <= ln["x0"] < ln["x1"] <= 1 and 0 <= ln["y0"] < ln["y1"] <= 1
            ws = ln["words"]  # per-word boxes drive the live highlighter
            assert ws and ws[0]["x0"] >= ln["x0"] - 1e-3 and ws[-1]["x1"] <= ln["x1"] + 1e-3
            assert all(a["x1"] <= b["x0"] + 1e-3 for a, b in zip(ws, ws[1:]))
    # bullets are separate paragraphs and sit below each other
    ys = [p["lines"][0]["y0"] for p in paras[1:]]
    assert ys == sorted(ys)


@pytest.mark.parametrize("slide,sentence,expected", [
    (3, "The dashboard refreshes every five seconds, so you always see what is selling right now.", "Live dashboard updated every 5 seconds"),
    (3, "Nimbus connects to Shopify or Square in about ten minutes, with no engineers needed.", "Plugs into Shopify and Square in 10 minutes"),
    (3, "The feature owners love most is automatic reorder alerts.", "Automatic reorder alerts"),
    (2, "The tools that exist cost more than two thousand dollars a month.", "Existing tools cost over 2,000 dollars per month"),
    (2, "So most owners end up deciding from gut feel instead of data.", "Most owners decide from gut feel"),
    (5, "On the roadmap, the mobile app ships in the fourth quarter, and multi-store rollups arrive in the first quarter of 2027.", "Mobile app ships in Q4"),
    (5, "Growth is one hundred forty-nine dollars.", "Starter 49 dollars, Growth 149 dollars, Scale 399 dollars"),
    (2, "Let's talk about The Problem.", "The Problem"),
])
def test_sentence_points_at_the_right_line(layout, slide, sentence, expected):
    hit = CursorMatcher(layout, HashEmbedder()).match(slide, sentence)
    assert hit is not None and hit["text"] == expected, hit


def test_unrelated_sentences_get_no_pointer(layout):
    m = CursorMatcher(layout, HashEmbedder())
    assert m.match(1, "Hi everyone, I'm glad to be walking you through this today.") is None
    assert m.match(3, "I would love to hear your questions on any of this.") is None
    assert m.match(99, "anything") is None


def test_placeholder_layout_geometry():
    lay = placeholder_layout([{"n": 1, "title": "Hello", "body": "first line\nsecond line"}])
    paras = lay[1]["paras"]
    assert [p["text"] for p in paras] == ["Hello", "first line", "second line"]
    assert paras[2]["lines"][0]["y0"] > paras[1]["lines"][0]["y0"]


async def test_ingest_writes_layout_and_cursor_map(svc):
    deck_id = await make_ready_deck(svc, audio=False)
    assert svc.store.layout(deck_id)
    cmap = svc.store.cursor_map(deck_id)
    narr = svc.store.narration(deck_id)
    assert set(cmap) == {str(n["n"]) for n in narr}
    assert all(len(cmap[str(n["n"])]) == len(n["sentences"]) for n in narr)
    hits = [b for v in cmap.values() for b in v if b]
    assert len(hits) >= 8  # most narration sentences point at a line


async def test_play_sentence_and_answers_carry_pointers(svc):
    deck_id = await make_ready_deck(svc)
    c = FakeClient(svc, play_s=0.25)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: len(c.of("play_sentence")) >= 6, timeout=30)
    withp = [m for m in c.of("play_sentence") if m["pointer"]]
    assert withp, "narration sentences must carry a pointer target"
    p = withp[0]["pointer"]
    assert 0 <= p["x0"] < p["x1"] <= 1 and 0 <= p["y0"] < p["y1"] <= 1 and p["text"]
    await asyncio.sleep(0.4)
    await c.sim("excuse me", final=False)
    await c.wait_state("LISTENING")
    await c.say("show me slide 5 what is the pricing for the growth plan")
    ans = await c.wait_for(lambda: c.of("play_answer") and c.of("play_answer"))
    assert all(a["slide_n"] == 5 for a in ans)  # answer is spoken while slide 5 is shown (goto_slide)
    assert any(a["pointer"] for a in ans), "answer sentences should point at the matching line"
    await c.close()


async def test_answer_about_another_slide_points_there_without_goto(svc):
    deck_id = await make_ready_deck(svc)
    c = FakeClient(svc, play_s=0.25)
    await c.open(deck_id)
    await c.ctl("start")
    await c.wait_for(lambda: c.of("play_sentence"))
    await asyncio.sleep(0.4)
    await c.sim("excuse me", final=False)
    await c.wait_state("LISTENING")
    await c.say("what are the starter growth and scale pricing tiers in dollars")
    ans = await c.wait_for(lambda: c.of("play_answer") and c.of("play_answer"))
    pricing = [a for a in ans if a["slide_n"] == 5 and a["pointer"]]
    assert pricing, [(a["slide_n"], a["pointer"], a["text"][:40]) for a in ans]
    assert "dollars" in pricing[0]["pointer"]["text"].lower()
    assert pricing[0]["pointer"]["lines"] and pricing[0]["pointer"]["lines"][0]["words"]
    await c.close()


def test_matched_line_flags_the_words_the_speaker_mentions(layout):
    hit = CursorMatcher(layout, HashEmbedder()).match(5, "Growth is one hundred forty-nine dollars a month.")
    line = hit["lines"][0]
    key_words = [w for w in line["words"] if w["key"]]
    assert 2 <= len(key_words) < len(line["words"])  # e.g. "Growth", "149", "dollars" - not every word
    assert hit["x0"] == line["x0"] and hit["text"].startswith("Starter 49 dollars")


def test_placeholder_layout_has_word_boxes():
    lay = placeholder_layout([{"n": 1, "title": "Two words", "body": "alpha beta gamma"}])
    ws = lay[1]["paras"][1]["lines"][0]["words"]
    assert [w["t"] for w in ws] == ["alpha", "beta", "gamma"]
    assert ws[0]["x1"] <= ws[1]["x0"] <= ws[1]["x1"] <= ws[2]["x0"]
