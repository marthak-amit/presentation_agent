"""Runtime settings. All model names come from the environment (.env / .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _load_env_files() -> None:
    # Real env wins, then .env, then .env.example (single source of non-secret defaults).
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.example", override=False)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = ""
    groq_qa_model: str = ""
    groq_script_model: str = ""
    groq_fallback_model: str = ""
    groq_reasoning_effort: str = ""
    groq_reasoning_model_prefix: str = ""
    deepgram_api_key: str = ""
    deepgram_stt_model: str = ""
    deepgram_tts_model: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = ""
    presenter_name: str = "Presenter"
    embedding_model: str = ""
    data_dir: Path = field(default_factory=lambda: ROOT / "backend" / "data")
    force_mock: frozenset[str] = frozenset()

    # Tunables (kept here so tests can shrink them).
    first_token_filler_s: float = 1.2
    first_token_timeout_s: float = 2.5
    followup_wait_s: float = 4.0
    barge_cooldown_s: float = 3.0
    barge_min_confidence: float = 0.7
    barge_fuzzy_threshold: int = 85
    echo_threshold: float = 0.6
    question_inline_words: int = 4
    utterance_min_words: int = 2

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        _load_env_files()
        data_dir = Path(_env("DATA_DIR") or (ROOT / "backend" / "data"))
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        s = cls(
            groq_api_key=_env("GROQ_API_KEY"),
            groq_qa_model=_env("GROQ_QA_MODEL"),
            groq_script_model=_env("GROQ_SCRIPT_MODEL"),
            groq_fallback_model=_env("GROQ_FALLBACK_MODEL"),
            groq_reasoning_effort=_env("GROQ_REASONING_EFFORT"),
            groq_reasoning_model_prefix=_env("GROQ_REASONING_MODEL_PREFIX"),
            deepgram_api_key=_env("DEEPGRAM_API_KEY"),
            deepgram_stt_model=_env("DEEPGRAM_STT_MODEL"),
            deepgram_tts_model=_env("DEEPGRAM_TTS_MODEL"),
            elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=_env("ELEVENLABS_VOICE_ID"),
            elevenlabs_model_id=_env("ELEVENLABS_MODEL_ID"),
            presenter_name=_env("PRESENTER_NAME", "Presenter") or "Presenter",
            embedding_model=_env("EMBEDDING_MODEL"),
            data_dir=data_dir,
            force_mock=frozenset(x.strip() for x in _env("FORCE_MOCK").split(",") if x.strip()),
        )
        return replace(s, **overrides) if overrides else s

    # --- derived paths ---
    @property
    def decks_dir(self) -> Path:
        return self.data_dir / "decks"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def stock_dir(self) -> Path:
        return self.data_dir / "stock"

    # --- which services are real ---
    @property
    def use_real_llm(self) -> bool:
        return bool(self.groq_api_key) and "llm" not in self.force_mock

    @property
    def use_real_stt(self) -> bool:
        return bool(self.deepgram_api_key) and "stt" not in self.force_mock

    @property
    def use_elevenlabs(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id) and "tts" not in self.force_mock

    @property
    def use_aura(self) -> bool:
        return bool(self.deepgram_api_key) and "tts" not in self.force_mock

    def qa_models(self) -> list[str]:
        out: list[str] = []
        for m in (self.groq_qa_model, self.groq_fallback_model):
            if m and m not in out:
                out.append(m)
        return out

    def script_models(self) -> list[str]:
        out: list[str] = []
        for m in (self.groq_script_model, self.groq_fallback_model):
            if m and m not in out:
                out.append(m)
        return out

    def ensure_dirs(self) -> None:
        for d in (self.decks_dir, self.chroma_dir, self.logs_dir, self.stock_dir):
            d.mkdir(parents=True, exist_ok=True)
