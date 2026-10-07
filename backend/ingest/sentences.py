"""Sentence splitting + narration cleanup (no markdown, speakable)."""
from __future__ import annotations

import re

_ABBR = ["Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "vs", "etc", "e.g", "i.e", "approx", "St", "Inc", "Ltd", "No", "Fig"]
_PLACEHOLDER = "․"  # one-dot leader, swapped back after splitting


def clean_narration(text: str) -> str:
    t = text.strip()
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|\*|`)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^(narration|script)\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"').strip("“”")
    return re.sub(r"\s+", " ", t).strip()


def split_sentences(text: str, min_words: int = 3) -> list[str]:
    t = clean_narration(text)
    if not t:
        return []
    for a in _ABBR:
        t = re.sub(rf"\b{re.escape(a)}\.", a + _PLACEHOLDER, t)
    t = re.sub(r"(?<=\d)\.(?=\d)", _PLACEHOLDER, t)  # 3.5
    t = re.sub(r"\b([A-Z])\.(?=[A-Z]\b)", r"\1" + _PLACEHOLDER, t)  # U.S.
    parts = re.split(r"(?<=[.!?…])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])", t)
    parts = [p.replace(_PLACEHOLDER, ".").strip() for p in parts if p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and len(p.split()) < min_words:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    if len(merged) > 1 and len(merged[0].split()) < min_words:
        merged[1] = f"{merged[0]} {merged[1]}"
        merged = merged[1:]
    return merged
