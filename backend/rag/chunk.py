"""Chunking for slides, notes, narration and extra docs."""
from __future__ import annotations

import re


def chunk_text(text: str, max_chars: int = 700, overlap: int = 100) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-*•#])", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > max_chars:  # split long paragraphs on sentence boundaries
            sents = re.split(r"(?<=[.!?])\s+", p)
        else:
            sents = [p]
        for s in sents:
            if cur and len(cur) + len(s) + 1 > max_chars:
                chunks.append(cur)
                cur = (cur[-overlap:] + " " + s) if overlap and len(s) < max_chars else s
                cur = cur.strip()
            else:
                cur = (cur + "\n" + s).strip() if cur else s
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for c in chunks:  # hard cap for pathological no-punctuation text
        while len(c) > max_chars * 2:
            out.append(c[:max_chars * 2])
            c = c[max_chars * 2 - overlap :]
        out.append(c)
    return out
