from __future__ import annotations

import shutil
import time

import pytest
from fastapi.testclient import TestClient

from backend.llm.client import FakeLLM
from backend.main import create_app
from backend.session.report import build_report

from .conftest import SAMPLE


def _ready_deck(http) -> str:
    with open(SAMPLE, "rb") as f:
        deck_id = http.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")},
                            data={"tone": "formal", "length": "short"}).json()["deck_id"]
    for _ in range(300):
        d = http.get(f"/decks/{deck_id}").json()
        if d["status"] == "ready" and d["audio"]["total"] and d["audio"]["done"] == d["audio"]["total"]:
            return deck_id
        time.sleep(0.1)
    raise AssertionError(d)


def test_upload_options_are_validated_and_stored(svc):
    with TestClient(create_app(svc)) as http:
        with open(SAMPLE, "rb") as f:
            r = http.post("/decks", files={"file": ("s.pptx", f, "application/octet-stream")}, data={"tone": "angry"})
        assert r.status_code == 400
        deck_id = _ready_deck(http)
        d = http.get(f"/decks/{deck_id}").json()
        assert d["tone"] == "formal" and d["length"] == "short"


def test_tone_and_length_reach_the_narration_prompt():
    from backend.llm.prompts import SlideBrief, narration_messages

    b = SlideBrief(n=2, total=5, title="T", body="b", tables="", notes="n")
    sysmsg = narration_messages("Bytes", b, tone="energetic", length="long", instruction="mention the pilot")[0]["content"]
    assert "upbeat" in sysmsg and "190 to 260 words" in sysmsg
    user = narration_messages("Bytes", b, instruction="mention the pilot", previous="Old text.")[1]["content"]
    assert "[EDITOR_INSTRUCTION]mention the pilot" in user and "[CURRENT_SCRIPT]\nOld text." in user


def test_edit_script_updates_everything_derived_from_it(svc):
    with TestClient(create_app(svc)) as http:
        deck_id = _ready_deck(http)
        before = http.get(f"/decks/{deck_id}").json()["slides"][2]
        n_old = len(before["sentences"])
        new_text = "Our brand new onboarding takes only ten minutes. Plugs into Shopify and Square. Zero engineers needed."
        r = http.put(f"/decks/{deck_id}/narration/3", json={"text": new_text})
        assert r.status_code == 200
        assert r.json()["sentences"] == ["Our brand new onboarding takes only ten minutes.", "Plugs into Shopify and Square.",
                                         "Zero engineers needed."]
        assert svc.store.narration(deck_id)[2]["sentences"][0].startswith("Our brand new onboarding")
        # retrieval sees the new text
        hits = http.get(f"/decks/{deck_id}/search", params={"q": "brand new onboarding ten minutes"}).json()
        assert any("brand new onboarding" in h["text"] for h in hits)
        # pointer map follows the new sentences and old orphan audio is gone
        assert len(svc.store.cursor_map(deck_id)["3"]) == 3
        for i in range(3, n_old):
            assert not svc.store.audio_path(deck_id, 3, i).exists()
        # changed sentences get fresh audio in the background
        for _ in range(100):
            d = http.get(f"/decks/{deck_id}").json()
            if all(d["slides"][2]["audio_urls"]):
                break
            time.sleep(0.1)
        assert all(http.get(f"/decks/{deck_id}").json()["slides"][2]["audio_urls"])
        # validation
        assert http.put(f"/decks/{deck_id}/narration/3", json={"sentences": ["", "  "]}).status_code == 400
        assert http.put(f"/decks/{deck_id}/narration/3", json={"sentences": ["x" * 700]}).status_code == 400
        assert http.put(f"/decks/{deck_id}/narration/99", json={"text": "hi there"}).status_code == 404


def test_regenerate_with_instruction_and_fallback(svc):
    class Echo(FakeLLM):
        async def complete(self, *, model, messages, max_tokens=1200, temperature=0.6):
            user = messages[-1]["content"]
            if "[EDITOR_INSTRUCTION]" in user:
                instr = user.split("[EDITOR_INSTRUCTION]")[1].split("[/EDITOR_INSTRUCTION]")[0]
                return f"Here is the rewritten script as asked: {instr}. It stays short and clear for the audience."
            return await super().complete(model=model, messages=messages)

    svc.llm = Echo("Amit", delay=0)
    with TestClient(create_app(svc)) as http:
        deck_id = _ready_deck(http)
        r = http.post(f"/decks/{deck_id}/narration/2/regenerate", json={"instruction": "make it punchy"})
        assert r.status_code == 200 and "make it punchy" in " ".join(r.json()["sentences"])
        assert http.post(f"/decks/{deck_id}/narration/2/regenerate", json={"tone": "rude"}).status_code == 400


def test_delete_deck_removes_files_and_index(svc):
    with TestClient(create_app(svc)) as http:
        deck_id = _ready_deck(http)
        assert svc.kb.count(deck_id) > 0
        assert http.delete(f"/decks/{deck_id}").status_code == 200
        assert http.get(f"/decks/{deck_id}").status_code == 404
        assert svc.kb.count(deck_id) == 0
        assert not svc.store.dir(deck_id).exists()
        assert http.delete(f"/decks/{deck_id}").status_code == 404


def test_report_markdown_lists_followups_first():
    rows = [
        {"ts": "2026-10-07T10:00:00.000+00:00", "question": "How much is Growth?", "answer": "It is 149 dollars.", "slide_n": 5,
         "trigger": "okay agent", "model": "m", "first_audio_ms": 640, "total_ms": 4100, "unanswered": False},
        {"ts": "2026-10-07T10:01:00.000+00:00", "question": "Do you support SSO?", "answer": "I'll have Bytes follow up.", "slide_n": 3,
         "trigger": "wait", "model": "m", "first_audio_ms": 700, "total_ms": 3900, "unanswered": True, "fallback": True},
    ]
    md = build_report("abc123", "Nimbus", "Bytes Technolab developer", rows)
    assert md.index("## Follow-ups to send") < md.index("## All questions")
    assert "- [ ] **Do you support SSO?** (slide 3)" in md and "2 question(s)" in md and "1 need a follow-up" in md
    assert "fallback model used" in md and "first audio 640 ms" in md
    assert "Nothing" in build_report("x", "n", "p", rows[:1])


def test_export_endpoint(svc):
    svc.logs.add_interruption("sess1", {"ts": "2026-10-07T10:00:00.000+00:00", "question": "Q?", "answer": "A.", "deck_id": "x",
                                        "unanswered": True, "slide_n": 1})
    with TestClient(create_app(svc)) as http:
        r = http.get("/sessions/sess1/export.md")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "- [ ] **Q?**" in r.text
        assert http.get("/sessions/bad..id/export.md").status_code in (400, 404)


def test_rewrite_all_changes_tone_for_an_existing_deck(svc):
    seen = []

    class Spy(FakeLLM):
        async def complete(self, *, model, messages, max_tokens=1200, temperature=0.6):
            seen.append(messages[0]["content"])
            return await super().complete(model=model, messages=messages)

    svc.llm = Spy("Amit", delay=0)
    with TestClient(create_app(svc)) as http:
        deck_id = _ready_deck(http)
        seen.clear()
        assert http.post(f"/decks/{deck_id}/narration/rewrite-all", json={"tone": "rude", "length": "short"}).status_code == 400
        r = http.post(f"/decks/{deck_id}/narration/rewrite-all", json={"tone": "storytelling", "length": "long"})
        assert r.status_code == 200
        for _ in range(200):
            d = http.get(f"/decks/{deck_id}").json()
            if d["rewrite"] and not d["rewrite"]["running"]:
                break
            time.sleep(0.1)
        assert d["rewrite"]["done"] == d["rewrite"]["total"] == 5 and d["rewrite"]["error"] is None
        assert d["tone"] == "storytelling" and d["length"] == "long"
        assert len(seen) == 5 and all("narrative" in s and "190 to 260 words" in s for s in seen)  # new style reached every slide
        assert http.get("/decks").json()[0]["tone"] == "storytelling"
