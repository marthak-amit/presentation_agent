"""Incremental sentence splitter for streamed LLM tokens."""
from __future__ import annotations

import re

from ..ingest.sentences import clean_narration

_BOUNDARY = re.compile(r"(?<=[.!?।॥])[\"')\]]*\s+(?=\S)")
_ABBR = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc|e\.g|i\.e|approx|No)\.$", re.I)


class SentenceStreamer:
    def __init__(self, min_words: int = 3):
        self.buf = ""
        self.min_words = min_words

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        start = 0
        for m in _BOUNDARY.finditer(self.buf):
            seg = self.buf[start:m.end()].strip()
            if _ABBR.search(seg) or len(seg.split()) < self.min_words:
                continue
            cleaned = clean_narration(seg)
            if cleaned:
                out.append(cleaned)
            start = m.end()
        self.buf = self.buf[start:]
        return out

    def flush(self) -> list[str]:
        rest = clean_narration(self.buf)
        self.buf = ""
        return [rest] if rest else []
