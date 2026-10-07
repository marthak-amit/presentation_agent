from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.ingest.store import DeckStore  # noqa: E402
from backend.llm.client import FakeLLM  # noqa: E402
from backend.services import Services, attach_tts  # noqa: E402
from backend.tts.chain import FallbackTTS  # noqa: E402
from backend.tts.fake import FakeTTS  # noqa: E402

SAMPLE = ROOT / "tests" / "sample_deck.pptx"


@pytest.fixture(scope="session", autouse=True)
def _sample_deck():
    if not SAMPLE.exists():
        from scripts.make_sample_deck import build

        build(SAMPLE)
    return SAMPLE


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings.from_env(
        data_dir=tmp_path / "data",
        groq_api_key="", deepgram_api_key="", elevenlabs_api_key="", elevenlabs_voice_id="",
        presenter_name="Amit",
        # Tests must be fast: shrink timers.
        followup_wait_s=0.4, barge_cooldown_s=0.3,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def svc(settings) -> Services:
    sv = Services(settings=settings, llm=FakeLLM("Amit", delay=0), store=DeckStore(settings), force_hash_embedder=True)
    attach_tts(sv, FallbackTTS([FakeTTS(speed=25.0)]))  # silent clips ~25x shorter than real speech
    return sv
