"""Chroma-backed knowledge base. search_kb() is the single retrieval entry point."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .chunk import chunk_text
from .embed import Embedder

log = logging.getLogger("rag")

MAX_CONTEXT_TOKENS = 1500


_STOP = set("a an the is are was were to of and or in on for with what how why when who which do does did you your it this that "
            "be can could would about me i we our they their there here from at as by per much many tell show please".split())
_TOK = re.compile(r"[a-z0-9]+")


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def keywords(text: str) -> set[str]:
    return {_stem(w) for w in _TOK.findall(text.lower()) if w not in _STOP and len(w) > 1}


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    text: str
    deck_id: str
    slide_n: int  # 0 for extra docs
    source: str  # slide | notes | narration | doc
    distance: float = 0.0
    doc_name: str = ""

    def label(self) -> str:
        if self.source == "doc":
            return f"document {self.doc_name}"
        return f"slide {self.slide_n} {self.source}"


class KnowledgeBase:
    def __init__(self, chroma_dir: Path, embedder: Embedder):
        import chromadb

        chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_dir))
        self.embedder = embedder
        # One collection per embedder so vectors from different models never mix.
        self._col = self._client.get_or_create_collection(
            name=f"kb_{embedder.name}".replace("/", "_")[:60], metadata={"hnsw:space": "cosine"}
        )
        self._lock = threading.Lock()

    # indexing ---------------------------------------------------------------
    def _add(self, deck_id: str, items: list[tuple[str, dict]]) -> int:
        if not items:
            return 0
        texts = [t for t, _ in items]
        ids = [
            hashlib.sha1(f"{deck_id}|{m['source']}|{m['slide_n']}|{m.get('doc_name','')}|{i}|{t}".encode()).hexdigest()
            for i, (t, m) in enumerate(items)
        ]
        vecs = self.embedder.embed(texts)
        with self._lock:
            self._col.upsert(ids=ids, documents=texts, embeddings=vecs,
                             metadatas=[{"deck_id": deck_id, **m} for _, m in items])
        return len(items)

    def index_slides(self, deck_id: str, slides: list[dict], narration: list[dict]) -> int:
        items: list[tuple[str, dict]] = []
        for s in slides:
            n = s["n"]
            label = f"Slide {n}: {s['title']}"
            body = "\n".join(x for x in (s.get("body"), s.get("tables")) if x)
            for c in chunk_text(f"{label}\n{body}" if body else label):
                items.append((c, {"slide_n": n, "source": "slide"}))
            for c in chunk_text(s.get("notes", "")):
                items.append((f"{label} (speaker notes)\n{c}", {"slide_n": n, "source": "notes"}))
        for nar in narration:
            text = " ".join(nar.get("sentences", []))
            for c in chunk_text(text):
                items.append((f"Slide {nar['n']}: {nar['title']} (narration)\n{c}", {"slide_n": nar["n"], "source": "narration"}))
        self.delete_deck(deck_id, sources=("slide", "notes", "narration"))
        return self._add(deck_id, items)

    def index_document(self, deck_id: str, name: str, text: str) -> int:
        items = [(c, {"slide_n": 0, "source": "doc", "doc_name": name}) for c in chunk_text(text)]
        return self._add(deck_id, items)

    def delete_deck(self, deck_id: str, sources: tuple[str, ...] | None = None) -> None:
        where: dict = {"deck_id": deck_id}
        if sources:
            where = {"$and": [{"deck_id": deck_id}, {"source": {"$in": list(sources)}}]}
        with self._lock:
            self._col.delete(where=where)

    # retrieval --------------------------------------------------------------
    def search(self, deck_id: str, query: str, k: int = 3) -> list[Chunk]:
        if not query.strip():
            return []
        vec = self.embedder.embed([query])[0]
        with self._lock:
            n = self._col.count()
            if n == 0:
                return []
            res = self._col.query(query_embeddings=[vec], n_results=min(max(k * 4, 12), n),
                                  where={"deck_id": deck_id})
        qk = keywords(query)
        scored: list[tuple[float, Chunk]] = []
        seen: set[str] = set()
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            key = doc[-120:]
            if key in seen:
                continue
            seen.add(key)
            lex = len(qk & keywords(doc)) / len(qk) if qk else 0.0
            # hybrid rank: vector similarity + keyword overlap (rescues numbers / proper nouns the embedder blurs)
            scored.append(((1.0 - float(dist)) + 0.6 * lex,
                           Chunk(doc, deck_id, int(meta.get("slide_n", 0)), meta.get("source", ""), float(dist),
                                 meta.get("doc_name", ""))))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _, c in scored[:k]]

    def count(self, deck_id: str) -> int:
        with self._lock:
            return len(self._col.get(where={"deck_id": deck_id}, include=[])["ids"])


def format_context(chunks: list[Chunk], max_tokens: int = MAX_CONTEXT_TOKENS) -> str:
    parts: list[str] = []
    used = 0
    for c in chunks:
        block = f"[{c.label()}]\n{c.text}"
        t = approx_tokens(block)
        if used + t > max_tokens:
            room = (max_tokens - used) * 4
            if room > 200:
                parts.append(block[:room])
            break
        parts.append(block)
        used += t
    return "\n\n".join(parts)
