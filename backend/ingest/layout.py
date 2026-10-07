"""Where is each line of text on each rendered slide? (for the animated pointer)

Real decks: `pdftotext -bbox-layout` on the LibreOffice PDF (poppler, already required for rendering).
Placeholder renders (no LibreOffice): synthesised from the known placeholder geometry.
All coordinates are normalised to 0..1 of the slide image.
"""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("ingest.layout")

_BULLETS = set("•▪◦●■◆◇○□➢➤✓✔–—-*·►▶")
_NUM_PREFIX = re.compile(r"^\(?\d{1,2}[.)]$")


def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_layout(pdf: Path) -> dict[int, dict]:
    """{slide_n: {"paras": [{"text", "lines": [{"text","x0","y0","x1","y1"}]}]}} for every PDF page."""
    out = subprocess.run(["pdftotext", "-bbox-layout", str(pdf), "-"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {out.stderr[:200]}")
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", out.stdout)
    root = ET.fromstring(xml)
    pages = [el for el in root.iter() if _strip(el.tag) == "page"]
    layout: dict[int, dict] = {}
    for n, page in enumerate(pages, start=1):
        pw, ph = float(page.get("width", 1)), float(page.get("height", 1))
        paras: list[dict] = []
        for block in (el for el in page.iter() if _strip(el.tag) == "block"):
            cur: dict | None = None
            for line in (el for el in block if _strip(el.tag) == "line"):
                words = [(w.text or "", float(w.get("xMin")), float(w.get("xMax"))) for w in line if _strip(w.tag) == "word"]
                if not words:
                    continue
                starts_bullet = words[0][0] in _BULLETS or bool(_NUM_PREFIX.match(words[0][0]))
                if starts_bullet and len(words) > 1:
                    words = words[1:]
                text = " ".join(w[0] for w in words).strip()
                if not text:
                    continue
                y0, y1 = float(line.get("yMin")), float(line.get("yMax"))
                box = {"text": text, "x0": round(words[0][1] / pw, 4), "x1": round(words[-1][2] / pw, 4),
                       "y0": round(y0 / ph, 4), "y1": round(y1 / ph, 4)}
                if cur is None or starts_bullet:
                    cur = {"text": text, "lines": [box]}
                    paras.append(cur)
                else:
                    cur["text"] += " " + text
                    cur["lines"].append(box)
        layout[n] = {"paras": paras}
    return layout


def placeholder_layout(slides: list[dict], size=(1280, 720)) -> dict[int, dict]:
    """Mirror of ingest.render.placeholder_png geometry."""
    w, h = size
    out: dict[int, dict] = {}
    for s in slides:
        paras = []
        title = f"{s['n']}. {s.get('title', '')}"[:60]
        paras.append({"text": s.get("title", ""), "lines": [
            {"text": s.get("title", ""), "x0": 60 / w, "x1": min(0.97, (60 + 30 * len(title)) / w), "y0": 50 / h, "y1": 115 / h}]})
        y = 160
        for line in s.get("body", "").splitlines()[:10]:
            t = line[:70]
            paras.append({"text": t, "lines": [{"text": t, "x0": 80 / w, "x1": min(0.97, (80 + 17 * (len(t) + 2)) / w),
                                                "y0": y / h, "y1": (y + 34) / h}]})
            y += 48
        out[s["n"]] = {"paras": paras}
    return out
