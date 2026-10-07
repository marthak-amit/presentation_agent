"""Spoken navigation commands, e.g. "Okay Agent, next slide" / "go to slide 3" / "pause" / "continue".

Matching is deliberately strict (whole utterance must be a command) so real questions such as
"what is on the next slide?" still go to the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..ingest.cursor import _num_words
from .barge_in import normalize

_FILLER = re.compile(r"\b(please|kindly|can you|could you|would you|will you|just|now|okay|ok|hey|agent|then|for me|thanks|thank you)\b")


@dataclass(frozen=True)
class Command:
    kind: str  # next | prev | goto | first | last | restart | repeat_slide | pause | resume
    slide: int | None = None


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("next", re.compile(r"^(go to |go on to |move on to |move to |show |show me |skip to )?(the )?next( slide)?$|^(skip|skip this|skip this slide|move on|go forward)$")),
    ("prev", re.compile(r"^(go )?back( one)?( slide)?$|^(go to |show |show me |move to |take me to )?(the )?(previous|prior|last but one)( slide)?$|^(go|move) back( to the previous slide)?$")),
    ("first", re.compile(r"^(go to |show |show me |take me to |jump to )?(the )?first slide$")),
    ("last", re.compile(r"^(go to |show |show me |take me to |jump to )?(the )?(last|final) slide$")),
    ("restart", re.compile(r"^(start over|restart|restart the presentation|from the beginning|start from the beginning|go to the beginning|begin again)$")),
    ("repeat_slide", re.compile(r"^(repeat|redo|start|go over|explain|say) (this|the|that|current) slide( again)?$|^(start this slide again|this slide again)$")),
    ("pause", re.compile(r"^(pause|pause the presentation|pause presentation|pause there|stop presenting|stop the presentation|hold the presentation|wait there)$")),
    ("resume", re.compile(r"^(continue|resume|continue presenting|resume presenting|go on|carry on|keep going|proceed|go ahead|keep presenting|repeat( that)?|say that again|start again|play)$")),
]
_GOTO = re.compile(r"^(go to |show |show me |jump to |open |take me to |move to |switch to |go back to )?(the )?slide (number |no |n )?(\d{1,3})$")


def parse_command(text: str, n_slides: int) -> Command | None:
    t = normalize(text)
    t = _FILLER.sub(" ", t)
    t = " ".join(t.split())
    if not t or len(t.split()) > 7:
        return None
    t = _num_words(t)
    m = _GOTO.match(t)
    if m:
        n = int(m.group(4))
        return Command("goto", n) if 1 <= n <= n_slides else None
    for kind, rx in _PATTERNS:
        if rx.match(t):
            return Command(kind)
    return None
