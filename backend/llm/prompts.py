"""Prompt builders. Sections use [MARKER] tags so the fake LLM can parse them too."""
from __future__ import annotations

from dataclasses import dataclass

NARRATION_SYSTEM = """You are ghost-writing the spoken script for a live presentation, in the voice of {presenter}.
Write what {presenter} will SAY out loud for one slide.

Rules:
- First person, as {presenter}. Conversational and natural, like talking to colleagues.
- Whenever the notes or slide use a personal name for the speaker, say {presenter} instead. Never use any other name for yourself.
- 120 to 180 words (about 45-75 seconds when spoken).
- The speaker notes are the PRIMARY source. Use slide text only to fill gaps. Never invent facts or numbers.
- Plain spoken prose only: no markdown, no bullet points, no lists, no headings, no stage directions, no emoji.
- Never say "on this slide", "this slide shows", "as you can see on the slide", or read the slide title aloud as a title.
- Spell out symbols so they are speakable ("percent", "dollars"). Keep sentences short enough to say in one breath.
- {position_rule}
- End with one smooth transition sentence leading into the next slide ("{next_hint}").
Output ONLY the script text."""

QA_SYSTEM = """You are {presenter}, presenting this deck live to an audience, and someone just asked you a question.
Answer in first person as {presenter}, in a natural spoken style.

Rules:
- At most about 75 words (roughly 30 seconds). No markdown, no lists, no headings, no emoji.
- Use ONLY the retrieved context provided. Never invent numbers, names, dates or claims.
- If the answer is not in the context, say briefly that you will have {presenter} follow up on it after the session. Do not guess.
- You may call search_kb(query) for more context, goto_slide(n) to show a relevant slide while you answer,
  and resume_presenting() only if the audience clearly asked you to continue with the presentation.
- Do not mention "context", "retrieval", "documents" or "the knowledge base"; speak as the presenter.
- Do not start with filler like "Great question"; just answer."""


@dataclass
class SlideBrief:
    n: int
    total: int
    title: str
    body: str
    tables: str
    notes: str
    next_title: str = ""
    next_body: str = ""


def narration_messages(presenter: str, s: SlideBrief) -> list[dict]:
    if s.n == 1:
        position_rule = f"This is the first slide: open with a brief warm greeting and introduce yourself as {presenter}."
    elif s.n == s.total:
        position_rule = "This is the final slide: wrap up the talk and invite questions at the end."
    else:
        position_rule = "This is a middle slide: do NOT greet the audience or re-introduce yourself."
    next_hint = (
        f"leading into: {s.next_title}" if s.next_title else "closing the talk"
    )
    system = NARRATION_SYSTEM.format(presenter=presenter, position_rule=position_rule, next_hint=next_hint)
    user = (
        f"[POSITION]{s.n}/{s.total}[/POSITION]\n"
        f"[PRESENTER]{presenter}[/PRESENTER]\n"
        f"[TITLE]{s.title}[/TITLE]\n"
        f"[BODY]\n{s.body or '(none)'}\n[/BODY]\n"
        f"[TABLES]\n{s.tables or '(none)'}\n[/TABLES]\n"
        f"[NOTES]\n{s.notes or '(none)'}\n[/NOTES]\n"
        f"[NEXT_TITLE]{s.next_title or '(end of talk)'}[/NEXT_TITLE]\n"
        f"[NEXT_BODY]\n{s.next_body or ''}\n[/NEXT_BODY]\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def qa_messages(
    presenter: str,
    question: str,
    context: str,
    history: list[tuple[str, str]] | None = None,
    slide_hint: str = "",
) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": QA_SYSTEM.format(presenter=presenter)}]
    for q, a in (history or [])[-3:]:
        msgs.append({"role": "user", "content": f"[QUESTION]{q}[/QUESTION]"})
        msgs.append({"role": "assistant", "content": a})
    user = (
        (f"[CURRENT_SLIDE]{slide_hint}[/CURRENT_SLIDE]\n" if slide_hint else "")
        + f"[CONTEXT]\n{context or '(nothing retrieved)'}\n[/CONTEXT]\n"
        + f"[QUESTION]{question}[/QUESTION]"
    )
    msgs.append({"role": "user", "content": user})
    return msgs
