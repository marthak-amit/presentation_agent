"""Markdown report of a presentation session: every question, the answer given and what needs a follow-up."""
from __future__ import annotations

from datetime import datetime


def _ms(v) -> str:
    return "–" if v in (None, "") else f"{int(v)} ms"


def build_report(session_id: str, deck_name: str, presenter: str, rows: list[dict]) -> str:
    answered = [r for r in rows if not r.get("unanswered")]
    open_items = [r for r in rows if r.get("unanswered")]
    when = rows[0]["ts"][:16].replace("T", " ") if rows else datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    out = [
        f"# Q&A report - {deck_name or 'presentation'}",
        "",
        f"*Presenter: {presenter} · {when} UTC · session `{session_id}`*",
        "",
        f"**{len(rows)} question(s)** · {len(answered)} answered · **{len(open_items)} need a follow-up**",
        "",
    ]
    out.append("## Follow-ups to send")
    out.append("")
    if open_items:
        for r in open_items:
            out.append(f"- [ ] **{r['question']}** (slide {r.get('slide_n', '?')})")
    else:
        out.append("_Nothing - every question was answered from the material._")
    out += ["", "## All questions", ""]
    for i, r in enumerate(rows, start=1):
        out.append(f"### {i}. {r['question']}")
        out.append("")
        out.append(f"> {r['answer']}")
        out.append("")
        meta = [f"slide {r.get('slide_n', '?')}", f"trigger: {r.get('trigger') or '–'}", f"model: {r.get('model') or '–'}",
                f"first audio {_ms(r.get('first_audio_ms'))}", f"total {_ms(r.get('total_ms'))}"]
        if r.get("fallback"):
            meta.append("fallback model used")
        if r.get("unanswered"):
            meta.append("**needs follow-up**")
        out.append("*" + " · ".join(meta) + "*")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
