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


def test_st_embedder_is_cpu_only_and_serialised(monkeypatch):
    """Regression: concurrent MiniLM calls on the Apple GPU (MPS) abort the process."""
    import sys
    import threading
    import time
    import types

    seen = {"device": None, "active": 0, "overlap": False}

    class FakeModel:
        def __init__(self, name, device=None):
            seen["device"] = device

        def encode(self, texts, **kw):
            seen["active"] += 1
            if seen["active"] > 1:
                seen["overlap"] = True
            time.sleep(0.02)
            seen["active"] -= 1
            import numpy as np

            return np.zeros((len(texts), 384))

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeModel))
    from backend.rag.embed import STEmbedder

    emb = STEmbedder("sentence-transformers/all-MiniLM-L6-v2")
    assert seen["device"] == "cpu"
    ts = [threading.Thread(target=emb.embed, args=(["x"],)) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not seen["overlap"]


async def test_deleting_a_deck_while_audio_is_generating_leaves_no_ghost(svc):
    """Regression: the trash icon left a blank 'ghost' entry when background audio was still being written."""
    import asyncio as aio

    from backend.services import attach_tts
    from backend.tts.chain import FallbackTTS
    from backend.tts.pregen import pregenerate_deck_audio

    class Slow:
        name = "elevenlabs"
        voice_key = "v"

        async def synth(self, text, *ctx):
            await aio.sleep(0.05)
            return b"\xff\xfb\x90\x00" + bytes(500)

    attach_tts(svc, FallbackTTS([Slow()]))
    deck_id = svc.store.create("sample")
    shutil.copy(SAMPLE, svc.store.source_path(deck_id))
    await run_ingest(svc, deck_id)
    task = aio.create_task(pregenerate_deck_audio(svc, deck_id))
    await aio.sleep(0.3)
    svc.store.delete(deck_id)
    await task
    assert svc.store.list_ids() == [] and not svc.store.exists(deck_id)
    assert svc.store.meta(deck_id) == {} or not svc.store.meta(deck_id).get("deck_id")


async def test_deleting_a_deck_during_ingest_stops_the_pipeline(svc):
    import asyncio as aio

    deck_id = svc.store.create("sample")
    shutil.copy(SAMPLE, svc.store.source_path(deck_id))
    t = aio.create_task(run_ingest(svc, deck_id))
    await aio.sleep(0.2)
    svc.store.delete(deck_id)
    await t
    assert svc.store.list_ids() == []


def test_existing_ghost_entries_are_cleaned_up(svc):
    ghost = svc.settings.decks_dir / "abcdef123456"
    ghost.mkdir(parents=True)
    (ghost / "meta.json").write_text('{"audio": {"done": 1}}')  # what the bug used to leave behind
    (svc.settings.decks_dir / "fedcba654321" / "audio").mkdir(parents=True)  # leftover without meta
    fresh = svc.settings.decks_dir / "0123456789ab"
    fresh.mkdir()  # a deck whose upload is happening right now: folder exists, meta.json is about to be written
    import os
    import time as _t

    old = _t.time() - 600
    for g in (ghost, svc.settings.decks_dir / "fedcba654321"):
        os.utime(g, (old, old))
    real = svc.store.create("real")
    assert svc.store.list_ids() == [real]
    assert not ghost.exists() and not (svc.settings.decks_dir / "fedcba654321").exists()
    assert fresh.exists()  # not mistaken for a ghost
