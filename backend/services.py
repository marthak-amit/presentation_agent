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


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    return Services(settings=settings, llm=make_llm(settings), store=DeckStore(settings))
