"""Match a spoken sentence to the line of the slide it is about (drives the animated pointer)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the is are was were to of and or in on for with what how why when who which do does did you your it this that "
            "be can could would about me i we our they their there here from at as by per much many so then also just very "
            "let lets us will more most all any some going get got have has had not but if than".split())
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def _num_words(text: str) -> str:
    """'forty-nine dollars' -> '49 dollars', 'two thousand' -> '2000' (enough to match spoken vs written numbers)."""
    toks = re.findall(r"[A-Za-z]+|\d[\d,\.]*|[^\sA-Za-z\d]", text.lower().replace("-", " "))
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        val = None
        if t in _TENS:
            val = _TENS[t]
            if i + 1 < len(toks) and toks[i + 1] in _ONES and _ONES[toks[i + 1]] < 10:
                val += _ONES[toks[i + 1]]
                i += 1
        elif t in _ONES:
            val = _ONES[t]
            if i + 1 < len(toks) and toks[i + 1] == "hundred":
                val *= 100
                i += 1
                if i + 1 < len(toks) and toks[i + 1] == "and":
                    i += 1
                if i + 1 < len(toks) and toks[i + 1] in _TENS:
                    val += _TENS[toks[i + 1]]
                    i += 1
                    if i + 1 < len(toks) and toks[i + 1] in _ONES and _ONES[toks[i + 1]] < 10:
                        val += _ONES[toks[i + 1]]
                        i += 1
                elif i + 1 < len(toks) and toks[i + 1] in _ONES:
                    val += _ONES[toks[i + 1]]
                    i += 1
        if val is not None:
            if i + 1 < len(toks) and toks[i + 1] == "thousand":
                val *= 1000
                i += 1
            out.append(str(val))
        else:
            out.append(t.replace(",", "") if t[:1].isdigit() else t)
        i += 1
    return " ".join(out)


def keywords(text: str) -> set[str]:
    return {(w[:-1] if len(w) > 3 and w.endswith("s") else w) for w in _WORD.findall(_num_words(text)) if w not in _STOP}


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


@dataclass
class _Para:
    text: str
    kw: set[str]
    lines: list[dict]
    vec: list[float] | None = None


class CursorMatcher:
    MIN_SCORE = 0.30

    def __init__(self, layout: dict, embedder=None):
        self.embedder = embedder
        self._slides: dict[int, tuple[list[_Para], dict[str, float]]] = {}
        self._raw = {int(k): v for k, v in layout.items()}

    def has_slide(self, n: int) -> bool:
        return bool(self._raw.get(n, {}).get("paras"))

    def _prep(self, n: int):
        if n in self._slides:
            return self._slides[n]
        paras = [_Para(p["text"], keywords(p["text"]), p["lines"]) for p in self._raw.get(n, {}).get("paras", [])]
        df: dict[str, int] = {}
        for p in paras:
            for w in p.kw:
                df[w] = df.get(w, 0) + 1
        idf = {w: math.log(1 + len(paras) / c) for w, c in df.items()}
        if self.embedder is not None and paras:
            try:
                for p, v in zip(paras, self.embedder.embed([p.text for p in paras])):
                    p.vec = v
            except Exception:
                self.embedder = None
        self._slides[n] = (paras, idf)
        return self._slides[n]

    def match(self, slide_n: int, sentence: str) -> dict | None:
        paras, idf = self._prep(slide_n)
        if not paras:
            return None
        sk = keywords(sentence)
        if not sk:
            return None
        svec = None
        if self.embedder is not None:
            try:
                svec = self.embedder.embed([sentence])[0]
            except Exception:
                svec = None
        order = {w: i for i, w in enumerate(w for w in dict.fromkeys(
            (x[:-1] if len(x) > 3 and x.endswith("s") else x) for x in _WORD.findall(_num_words(sentence))))}
        scored: list[tuple[float, _Para]] = []
        for p in paras:
            if not p.kw:
                continue
            tot = sum(idf.get(w, 1.0) for w in p.kw)
            common = p.kw & sk
            lex = sum(idf.get(w, 1.0) for w in common) / tot if tot else 0.0
            if len(common) >= 2:  # short sentence quoting part of a long line ("Growth is 149")
                lex = max(lex, 0.85 * len(common) / len(sk))
            if len(p.kw) < 2:
                lex *= 0.6  # one-word fragments ("Tier") match too easily
            sem = max(0.0, _cos(svec, p.vec)) if svec is not None and p.vec is not None else 0.0
            scored.append((0.7 * lex + 0.3 * sem * (1.0 if lex > 0 else 0.5), p))
        if not scored:
            return None
        best_score = max(sc for sc, _ in scored)
        if best_score < self.MIN_SCORE:
            return None
        # near-ties: point at whichever line the sentence mentions first (it is being read out in that order)
        close = [(sc, p) for sc, p in scored if sc >= 0.85 * best_score]
        best_score, best = min(close, key=lambda t: min((order[w] for w in t[1].kw & sk if w in order), default=999))
        # short wrapped paragraphs are highlighted whole (read line by line); long ones only the best line
        if len(best.lines) <= 3:
            chosen = best.lines
        else:
            chosen = [max(best.lines, key=lambda ln: len(keywords(ln["text"]) & sk))]
        lines = []
        for ln in chosen:
            words = [{"x0": w["x0"], "x1": w["x1"], "key": bool(keywords(w["t"]) & sk)} for w in ln.get("words", [])]
            lines.append({"x0": ln["x0"], "y0": ln["y0"], "x1": ln["x1"], "y1": ln["y1"], "words": words})
        first = lines[0]
        return {"x0": first["x0"], "y0": first["y0"], "x1": first["x1"], "y1": first["y1"], "text": best.text,
                "lines": lines, "score": round(best_score, 3)}


def build_sentence_map(matcher: CursorMatcher, narration: list[dict]) -> dict[str, list[dict | None]]:
    return {str(s["n"]): [matcher.match(s["n"], t) for t in s["sentences"]] for s in narration}
