"""Slide rendering: LibreOffice headless -> PDF -> pdf2image PNGs (Pillow placeholder fallback)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("ingest.render")


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def pptx_to_pdf(pptx: Path, out_dir: Path, timeout: int = 180) -> Path:
    exe = _soffice()
    if not exe:
        raise RuntimeError("LibreOffice (soffice) not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo_profile_") as profile:
        cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    pdf = out_dir / (pptx.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"soffice produced no PDF: {r.stderr[-300:] or r.stdout[-300:]}")
    return pdf


def pdf_to_pngs(pdf: Path, slides_dir: Path, dpi: int = 110) -> int:
    from pdf2image import convert_from_path

    slides_dir.mkdir(parents=True, exist_ok=True)
    pages = convert_from_path(str(pdf), dpi=dpi, fmt="png")
    for i, img in enumerate(pages, start=1):
        img.save(slides_dir / f"{i}.png", "PNG")
    return len(pages)


def placeholder_png(path: Path, n: int, title: str, body: str, size=(1280, 720)) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, (24, 28, 40))
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((60, 50), f"{n}. {title}"[:60], fill=(240, 240, 250), font=big)
    y = 160
    for line in body.splitlines()[:10]:
        d.text((80, y), "• " + line[:70], fill=(190, 196, 215), font=small)
        y += 48
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def render_slides(pptx: Path, slides_dir: Path, slide_meta: list[dict]) -> str:
    """Render PNGs for every slide. Returns 'libreoffice' or 'placeholder'."""
    try:
        with tempfile.TemporaryDirectory(prefix="render_") as tmp:
            pdf = pptx_to_pdf(pptx, Path(tmp))
            count = pdf_to_pngs(pdf, slides_dir)
        if count >= len(slide_meta):
            return "libreoffice"
        log.warning("render produced %d pages for %d slides; filling gaps", count, len(slide_meta))
    except Exception as e:  # never block ingest on rendering
        log.warning("slide render failed (%s); using placeholders", e)
        mode = "placeholder"
    else:
        mode = "libreoffice"
    for s in slide_meta:
        p = slides_dir / f"{s['n']}.png"
        if not p.exists():
            placeholder_png(p, s["n"], s.get("title", ""), s.get("body", ""))
            mode = "placeholder" if mode != "libreoffice" else "libreoffice+placeholder"
    return mode
