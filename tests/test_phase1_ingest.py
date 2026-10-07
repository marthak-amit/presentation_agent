from __future__ import annotations

import shutil
import time

import pytest
from fastapi.testclient import TestClient

from backend.ingest.narration import generate_slide_narration
from backend.ingest.parse import parse_pptx
from backend.ingest.pipeline import run_ingest
from backend.ingest.sentences import clean_narration, split_sentences
from backend.llm.prompts import SlideBrief
from backend.llm.retry import with_backoff
from backend.main import create_app
from backend.rag.chunk import chunk_text
from backend.rag.store import format_context

from .conftest import SAMPLE


# ---------------------------------------------------------------- parsing
def test_parse_pptx_extracts_title_body_table_notes():
    slides = parse_pptx(SAMPLE)
    assert len(slides) == 5
    assert slides[0].title.startswith("Nimbus Analytics")
    assert "stockouts" in slides[1].body
    assert "Growth | 17 | 2,533" in slides[3].tables
    assert "mobile app ships" in slides[4].notes
    assert all(s.notes for s in slides)


# ---------------------------------------------------------------- sentences
def test_split_sentences_handles_abbreviations_and_decimals():
    text = "Dr. Smith said revenue grew 3.5 percent in the U.S. last year. We think that is great! Is it sustainable? Yes."
    s = split_sentences(text)
    assert s[0].startswith("Dr. Smith") and "3.5" in s[0] and s[0].endswith("last year.")
    assert len(s) == 3  # "Yes." merged into previous sentence


def test_clean_narration_strips_markdown():
    out = clean_narration("**Hello** everyone.\n- point one\n- point two\n# Heading\n`code`")
    assert "*" not in out and "#" not in out and "`" not in out and "- " not in out


def test_chunk_text_respects_size():
    chunks = chunk_text("Sentence number one is here. " * 80, max_chars=300)
    assert len(chunks) > 3 and all(len(c) <= 600 for c in chunks)


# ---------------------------------------------------------------- 429 backoff
async def test_backoff_retries_on_429_then_succeeds():
    class RL(Exception):
        status_code = 429

    calls, sleeps = [], []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RL("rate limited")
        return "ok"

    async def fake_sleep(s):
        sleeps.append(s)

    assert await with_backoff(fn, sleep=fake_sleep) == "ok"
    assert sleeps == [2.0, 4.0]


async def test_backoff_does_not_retry_other_errors():
    async def fn():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await with_backoff(fn, sleep=lambda s: None)


async def test_narration_falls_back_when_models_fail(settings):
    class Broken:
        name = "broken"

        async def complete(self, **kw):
            raise RuntimeError("down")

    settings2 = settings.__class__(**{**settings.__dict__, "groq_script_model": "m1", "groq_fallback_model": "m2"})
    brief = SlideBrief(n=2, total=5, title="T", body="b", tables="", notes="We grew a lot this year. It was good.",
                       next_title="Next")
    text, model = await generate_slide_narration(Broken(), settings2, brief)
    assert model == "fallback-template" and "grew a lot" in text


# ---------------------------------------------------------------- pipeline + API
async def test_pipeline_end_to_end(svc):
    deck_id = svc.store.create("sample")
    shutil.copy(SAMPLE, svc.store.source_path(deck_id))
    await run_ingest(svc, deck_id)
    meta = svc.store.meta(deck_id)
    assert meta["status"] == "ready", meta
    narr = svc.store.narration(deck_id)
    assert [n["n"] for n in narr] == [1, 2, 3, 4, 5]
    assert all(len(n["sentences"]) >= 3 for n in narr)
    assert any("Amit" in s for s in narr[0]["sentences"])
    for n in range(1, 6):
        assert svc.store.slide_image(deck_id, n).stat().st_size > 1000
    hits = svc.kb.search(deck_id, "pricing for the Starter Growth and Scale tiers", k=3)
    assert hits and any(h.slide_n == 5 for h in hits)
    assert len(format_context(hits)) < 6000 + 500


def test_api_upload_poll_get_and_docs(svc):
    app = create_app(svc)
    with TestClient(app) as c:
        with open(SAMPLE, "rb") as f:
            r = c.post("/decks", files={"file": ("sample_deck.pptx", f, "application/octet-stream")})
        assert r.status_code == 200
        deck_id = r.json()["deck_id"]
        for _ in range(200):
            d = c.get(f"/decks/{deck_id}").json()
            if d["status"] != "processing":
                break
            time.sleep(0.1)
        assert d["status"] == "ready", d
        assert len(d["slides"]) == 5
        s1 = d["slides"][0]
        assert s1["n"] == 1 and s1["title"] and s1["sentences"] and s1["image_url"].endswith("/slides/1.png")
        img = c.get(s1["image_url"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"

        # extra docs
        r = c.post(f"/decks/{deck_id}/docs", files=[("files", ("faq.md", b"# FAQ\nOur SOC2 audit completed in August 2026 with zero findings.", "text/markdown"))])
        assert r.status_code == 200 and r.json()["chunks"] >= 1
        hits = c.get(f"/decks/{deck_id}/search", params={"q": "SOC2 audit"}).json()
        assert any("SOC2" in h["text"] for h in hits)
        # bad type / bad deck
        assert c.post(f"/decks/{deck_id}/docs", files=[("files", ("x.exe", b"zz", "application/octet-stream"))]).status_code == 400
        assert c.get("/decks/deadbeefdead").status_code == 404
        assert c.post("/decks", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400
        assert any(d["deck_id"] == deck_id for d in c.get("/decks").json())


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice not installed")
def test_real_libreoffice_render(tmp_path):
    from backend.ingest.render import render_slides

    meta = [{"n": i, "title": "t", "body": ""} for i in range(1, 6)]
    mode = render_slides(SAMPLE, tmp_path / "slides", meta)
    assert mode == "libreoffice"
    assert all((tmp_path / "slides" / f"{i}.png").exists() for i in range(1, 6))
