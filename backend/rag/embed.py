"""Embedders: sentence-transformers (local, free) with a deterministic hashing fallback."""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from typing import Protocol

log = logging.getLogger("rag.embed")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Bag-of-words + char-trigram hashing into 384 dims. Mock for when the model can't be downloaded."""

    name = "hash384"
    dim = 384

    _tok = re.compile(r"[a-z0-9]+")

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        words = self._tok.findall(text.lower())
        feats = [(w, 1.0) for w in words]
        for w in words:
            if len(w) > 4:
                feats += [(w[i : i + 3], 0.3) for i in range(len(w) - 2)]
        feats += [(a + "_" + b, 0.5) for a, b in zip(words, words[1:])]
        for f, wt in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "little")
            v[h % self.dim] += wt
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class STEmbedder:
    dim = 384

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        # CPU on purpose: MiniLM is tiny, and torch's Apple-GPU (MPS) path aborts the whole process
        # ("failed assertion ... IOGPUMetalCommandBuffer") when called from several threads.
        self._m = SentenceTransformer(model_name, device="cpu")
        self._lock = threading.Lock()  # retrieval and pointer matching embed from different threads
        self.name = "st-" + model_name.split("/")[-1]

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return self._m.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def make_embedder(model_name: str, force_hash: bool = False) -> Embedder:
    if force_hash or not model_name:
        return HashEmbedder()
    try:
        e = STEmbedder(model_name)
        log.info("using sentence-transformers embedder %s", model_name)
        return e
    except Exception as ex:  # not installed / offline / blocked
        log.warning("sentence-transformers unavailable (%s) -> HashEmbedder (mock)", ex)
        return HashEmbedder()
