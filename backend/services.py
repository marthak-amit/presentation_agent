"""Service container: wires settings + real/mock providers. Tests can build their own."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from .config import Settings
from .ingest.store import DeckStore
from .llm.client import LLMClient, make_llm
from .rag.embed import make_embedder
from .rag.store import KnowledgeBase

log = logging.getLogger("services")


@dataclass
class Services:
    settings: Settings
    llm: LLMClient
    store: DeckStore
    force_hash_embedder: bool = False
    _kb: KnowledgeBase | None = None
    _kb_lock: threading.Lock = field(default_factory=threading.Lock)
    extras: dict = field(default_factory=dict)  # phase 2+: tts, stt factory, hub, logs

    @property
    def kb(self) -> KnowledgeBase:
        if self._kb is None:
            with self._kb_lock:
                if self._kb is None:
                    emb = make_embedder(self.settings.embedding_model, force_hash=self.force_hash_embedder)
                    self._kb = KnowledgeBase(self.settings.chroma_dir, emb)
        return self._kb

    def __getattr__(self, item):  # services.tts / services.hub ... live in extras
        extras = self.__dict__.get("extras", {})
        if item in extras:
            return extras[item]
        raise AttributeError(item)


def attach_tts(svc: Services, tts=None) -> Services:
    """Wire TTS + audio cache (real chain by default; tests pass a FallbackTTS around FakeTTS)."""
    from .tts.cache import AudioCache
    from .tts.chain import make_tts

    tts = tts or make_tts(svc.settings)
    svc.extras["tts"] = tts
    svc.extras["audio"] = AudioCache(tts)
    return svc


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    svc = Services(settings=settings, llm=make_llm(settings), store=DeckStore(settings))
    attach_tts(svc)
    return svc
