"""The deck as seen by a session: slides + sentences, and playhead arithmetic."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlidePlan:
    n: int
    title: str
    sentences: list[str]


class DeckPlan:
    def __init__(self, deck_id: str, slides: list[SlidePlan]):
        self.deck_id = deck_id
        self.slides = slides
        self._by_n = {s.n: s for s in slides}

    @classmethod
    def load(cls, store, deck_id: str) -> "DeckPlan":
        narr = {n["n"]: n for n in store.narration(deck_id)}
        slides = [
            SlidePlan(s["n"], s["title"], list(narr.get(s["n"], {}).get("sentences", [])))
            for s in store.slides(deck_id)
        ]
        return cls(deck_id, slides)

    def __len__(self) -> int:
        return len(self.slides)

    @property
    def first(self) -> int:
        return self.slides[0].n

    @property
    def last(self) -> int:
        return self.slides[-1].n

    def has(self, n: int) -> bool:
        return n in self._by_n

    def slide(self, n: int) -> SlidePlan:
        return self._by_n[n]

    def title(self, n: int) -> str:
        return self._by_n[n].title if n in self._by_n else ""

    def sentence(self, n: int, i: int) -> str | None:
        s = self._by_n.get(n)
        if s is None or i < 0 or i >= len(s.sentences):
            return None
        return s.sentences[i]

    def next_pos(self, n: int, i: int) -> tuple[int, int] | None:
        """Position after (n, i), or None at the end of the deck."""
        s = self._by_n[n]
        if i + 1 < len(s.sentences):
            return n, i + 1
        idx = self.slides.index(s)
        if idx + 1 < len(self.slides):
            return self.slides[idx + 1].n, 0
        return None

    def prev_slide(self, n: int) -> int:
        idx = self.slides.index(self._by_n[n])
        return self.slides[max(0, idx - 1)].n

    def next_slide(self, n: int) -> int:
        idx = self.slides.index(self._by_n[n])
        return self.slides[min(len(self.slides) - 1, idx + 1)].n
