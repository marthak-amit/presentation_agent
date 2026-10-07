"""Presenter state machine.

PRESENTING -> PAUSED -> LISTENING -> ANSWERING -> PRESENTING;  END -> OPEN_QA.
"""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger("session.state")


class S(str, Enum):
    IDLE = "IDLE"
    PRESENTING = "PRESENTING"
    PAUSED = "PAUSED"
    LISTENING = "LISTENING"
    ANSWERING = "ANSWERING"
    END = "END"
    OPEN_QA = "OPEN_QA"


ALLOWED: dict[S, set[S]] = {
    S.IDLE: {S.PRESENTING},
    S.PRESENTING: {S.PAUSED, S.END, S.IDLE},
    S.PAUSED: {S.PRESENTING, S.LISTENING, S.IDLE},
    S.LISTENING: {S.ANSWERING, S.PRESENTING, S.OPEN_QA, S.PAUSED, S.IDLE},
    S.ANSWERING: {S.LISTENING, S.PRESENTING, S.OPEN_QA, S.IDLE},
    S.END: {S.OPEN_QA, S.IDLE, S.PRESENTING},
    S.OPEN_QA: {S.ANSWERING, S.PRESENTING, S.IDLE},
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    def __init__(self):
        self.state = S.IDLE
        self.history: list[tuple[S, S, str]] = []

    def can(self, to: S) -> bool:
        return to == self.state or to in ALLOWED[self.state]

    def go(self, to: S, reason: str = "") -> bool:
        """Transition. Returns True if the state changed. Raises on illegal transitions."""
        if to == self.state:
            return False
        if to not in ALLOWED[self.state]:
            raise InvalidTransition(f"{self.state.value} -> {to.value} ({reason})")
        self.history.append((self.state, to, reason))
        log.info("state %s -> %s (%s)", self.state.value, to.value, reason)
        self.state = to
        return True
