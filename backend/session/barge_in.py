"""Voice barge-in: trigger-phrase detection, echo guard, cooldown, utterance buffering.

Pure logic (no I/O) so it is unit-testable and fast: detection cost is a handful of rapidfuzz calls,
well inside the 400 ms budget.
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

TRIGGERS = [
    "i have a question", "quick question", "can i ask", "excuse me", "wait", "hold on",
    "sorry to interrupt", "one question", "ek sawaal", "ek minute", "ruko", "mera question",
    "ek prashn", "ubho raho",
]
# Deepgram `language=multi` may return Hindi in Devanagari script; same phrases, different script.
EXTRA_TRIGGERS = ["एक सवाल", "एक मिनट", "रुको", "मेरा सवाल", "मेरा क्वेश्चन", "एक प्रश्न"]
# Wake phrase: "Hello One" (STT may write it "Hello, 1." / "hello won" / "hello wan"), then the question.
WAKE_TRIGGERS = ["hello one", "hello 1", "hello won", "hello wan"]
ALL_TRIGGERS = TRIGGERS + EXTRA_TRIGGERS + WAKE_TRIGGERS
# Short wake phrases sit close to ordinary speech ("hello once", "hello on"), so they need a tight match.
STRICT_THRESHOLD = 96
STRICT_TRIGGERS = set(WAKE_TRIGGERS)

DISMISSALS = {
    "no", "nope", "nothing", "thanks", "thank you", "that's all", "thats all", "that's it", "thats it", "go on",
    "continue", "carry on", "keep going", "all good", "no that's fine", "no thanks", "nahi", "theek hai", "ok", "okay",
    "no thank you", "that is all", "i'm good", "im good", "we're good", "no it's fine", "no that is fine",
}


@dataclass
class Normalized:
    """Lowercased, punctuation-free word list that remembers where each word was in the original text."""
    words: list[str]
    spans: list[tuple[int, int]]  # (start, end) in the original string
    joined: str = ""

    @classmethod
    def of(cls, text: str) -> "Normalized":
        words: list[str] = []
        spans: list[tuple[int, int]] = []
        start = None
        buf: list[str] = []
        for idx, ch in enumerate(text):
            keep = not (ch.isspace() or unicodedata.category(ch)[0] in "PS") or ch == "'"
            if keep:
                if start is None:
                    start = idx
                buf.append(ch.lower())
            elif start is not None:
                words.append("".join(buf))
                spans.append((start, idx))
                start, buf = None, []
        if start is not None:
            words.append("".join(buf))
            spans.append((start, len(text)))
        words = [w.replace("'", "") for w in words]
        return cls(words, spans, " ".join(words))

    def word_index_at(self, joined_pos: int) -> int:
        """Index of the word containing character `joined_pos` of `joined`."""
        return self.joined.count(" ", 0, max(0, joined_pos))


def normalize(text: str) -> str:
    return Normalized.of(text).joined


@dataclass
class TriggerMatch:
    trigger: str
    score: float
    start_char: int  # offset in the ORIGINAL text where the trigger begins
    end_word: int  # index just past the last trigger word in the normalized word list
    words_after: int


def find_trigger(text: str, triggers: list[str] = ALL_TRIGGERS, threshold: int = 85) -> TriggerMatch | None:
    """Best trigger match in `text` (fuzzy, rapidfuzz partial_ratio >= threshold), or None."""
    nt = Normalized.of(text)
    if not nt.words:
        return None
    best: TriggerMatch | None = None
    for trig in triggers:
        tw = normalize(trig)
        n_tw = len(tw.split())
        if n_tw == 1:
            # single word: compare to whole words only, so "wait" doesn't fire inside "waiting"/"await"
            for wi, w in enumerate(nt.words):
                sc = fuzz.ratio(tw, w)
                if sc >= threshold and (best is None or sc > best.score):
                    best = _mk(nt, trig, sc, wi, wi + 1)
            continue
        # Avoid matching a half-spoken trigger ("i have") by requiring most of the phrase to be present.
        if len(nt.joined) < int(len(tw) * (1.0 if trig in STRICT_TRIGGERS else 0.85)):
            continue
        al = fuzz.partial_ratio_alignment(tw, nt.joined)
        if al is None or al.score < max(threshold, STRICT_THRESHOLD if trig in STRICT_TRIGGERS else 0):
            continue
        # a wake phrase must sit on word boundaries
        if trig in STRICT_TRIGGERS and (
            (al.dest_start > 0 and nt.joined[al.dest_start - 1] != " ")
            or (al.dest_end < len(nt.joined) and nt.joined[al.dest_end] != " ")
        ):
            continue  # "shello one" / "hello once" / "hello 10" are not the wake phrase
        first = nt.word_index_at(al.dest_start)
        if al.dest_start < len(nt.joined) and nt.joined[al.dest_start] == " ":
            first += 1
        last = nt.word_index_at(max(al.dest_end - 1, 0)) + 1
        last = max(last, first + 1)
        if best is None or al.score > best.score or (al.score == best.score and n_tw > len(best.trigger.split())):
            best = _mk(nt, trig, al.score, first, min(last, len(nt.words)))
    return best


def _mk(nt: Normalized, trig: str, score: float, first: int, end: int) -> TriggerMatch:
    return TriggerMatch(trigger=trig, score=float(score), start_char=nt.spans[first][0], end_word=end,
                        words_after=len(nt.words) - end)


def strip_leading_trigger(text: str) -> str:
    """Remove a trigger phrase near the start of an utterance: 'I have a question about X' -> 'about X'."""
    m = find_trigger(text)
    if m is None or m.start_char > 20:
        return text.strip()
    nt = Normalized.of(text)
    if m.end_word >= len(nt.spans):
        return ""
    rest = text[nt.spans[m.end_word][0]:].strip()
    return rest


def is_dismissal(text: str) -> bool:
    n = normalize(text)
    if not n:
        return True
    if n in DISMISSALS:
        return True
    words = n.split()
    return len(words) <= 4 and any(n.startswith(d) for d in DISMISSALS if len(d.split()) <= 2) and not any(
        w in n for w in ("what", "how", "why", "when", "where", "who", "which", "can", "does", "do", "is")
    )


def similarity(a: str, b: str) -> float:
    """0..1 echo similarity of transcript `a` vs spoken sentence `b`."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if len(na.split()) < 3:
        # one/two-word transcripts only count as echo on an exact word run (e.g. a lone "wait" is NOT echo)
        return 1.0 if f" {na} " in f" {nb} " and len(nb.split()) <= 3 else 0.0
    # partial_ratio alone over-matches (a trigger phrase can sit inside an unrelated sentence), so scale it by the
    # fraction of transcript words that actually occur in the spoken sentence.
    sw = nb.split()
    tw = na.split()
    overlap = sum(1 for w in tw if any(fuzz.ratio(w, x) >= 80 for x in sw)) / len(tw)
    return fuzz.partial_ratio(na, nb) / 100.0 * overlap


class EchoGuard:
    """Remembers what the agent is saying so its own voice leaking into the mic isn't taken as the user."""

    def __init__(self, threshold: float = 0.6, keep: int = 3):
        self.threshold = threshold
        self.recent: list[str] = []
        self.keep = keep
        self.last_end = 0.0

    def speaking(self, text: str) -> None:
        self.recent.append(text)
        self.recent = self.recent[-self.keep:]

    def mark_end(self, now: float) -> None:
        self.last_end = now

    def is_echo(self, transcript: str) -> bool:
        if not self.recent:
            return False
        # the sentence currently playing and the one before it (STT lags audio)
        return any(similarity(transcript, s) > self.threshold for s in self.recent[-2:])


@dataclass
class BargeDecision:
    match: TriggerMatch
    detect_ms: float
    treat_as_question: bool  # > N words already spoken after the trigger -> skip "go ahead"


class BargeInDetector:
    def __init__(self, *, threshold: int = 85, min_confidence: float = 0.7, cooldown_s: float = 3.0,
                 inline_words: int = 4, echo: EchoGuard | None = None, clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.min_confidence = min_confidence
        self.cooldown_s = cooldown_s
        self.inline_words = inline_words
        self.echo = echo or EchoGuard()
        self.clock = clock
        self.resumed_at = -1e9
        self.hits = 0
        self.last_reject: tuple[str, str] | None = None

    def note_resume(self) -> None:
        self.resumed_at = self.clock()

    def check(self, text: str, confidence: float, *, received_at: float | None = None) -> BargeDecision | None:
        t0 = received_at if received_at is not None else self.clock()
        self.last_reject: tuple[str, str] | None = None
        m = find_trigger(text, threshold=self.threshold)
        if m is None:
            return None
        if confidence < self.min_confidence:
            self.last_reject = (m.trigger, f"confidence {confidence:.2f} < {self.min_confidence}")
            return None
        if self.clock() - self.resumed_at < self.cooldown_s:
            self.last_reject = (m.trigger, "cooldown after resume")
            return None
        if self.echo.is_echo(text):
            self.last_reject = (m.trigger, "echo of current narration")
            return None
        self.hits += 1
        return BargeDecision(m, (self.clock() - t0) * 1000.0, m.words_after > self.inline_words)


@dataclass
class UtteranceBuffer:
    """Accumulates finals + current interim for the utterance being spoken."""
    finals: list[str] = field(default_factory=list)
    interim: str = ""
    capturing: bool = False
    _seed_pending: bool = False

    def reset(self) -> None:
        self.finals, self.interim, self.capturing, self._seed_pending = [], "", False, False

    def seed_from_trigger(self, interim_text: str, start_char: int) -> None:
        """Start capturing at the trigger phrase of the interim that fired the barge-in."""
        self.finals = []
        self.interim = interim_text[start_char:].strip()
        self.capturing = True
        self._seed_pending = True

    def add(self, text: str, is_final: bool) -> None:
        if not self.capturing:
            return
        if is_final:
            if self._seed_pending:
                self._seed_pending = False
                m = find_trigger(text)
                if m is not None:
                    text = text[m.start_char:]
            self.finals.append(text.strip())
            self.interim = ""
        else:
            self.interim = text.strip()

    def text(self) -> str:
        return " ".join(x for x in [*self.finals, self.interim] if x).strip()
