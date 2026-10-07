"""On-disk deck layout: data/decks/{id}/{meta.json,slides.json,narration.json,slides/,audio/}."""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ..config import Settings


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path: Path, obj: Any) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")


class DeckStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.decks_dir.mkdir(parents=True, exist_ok=True)

    # ids / paths ------------------------------------------------------------
    @staticmethod
    def valid_id(deck_id: str) -> bool:
        return bool(_ID_RE.match(deck_id))

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def dir(self, deck_id: str) -> Path:
        if not self.valid_id(deck_id):
            raise KeyError(deck_id)
        return self.settings.decks_dir / deck_id

    def exists(self, deck_id: str) -> bool:
        return self.valid_id(deck_id) and (self.dir(deck_id) / "meta.json").exists()

    def source_path(self, deck_id: str) -> Path:
        return self.dir(deck_id) / "source.pptx"

    def slides_dir(self, deck_id: str) -> Path:
        return self.dir(deck_id) / "slides"

    def slide_image(self, deck_id: str, n: int) -> Path:
        return self.slides_dir(deck_id) / f"{n}.png"

    def audio_path(self, deck_id: str, n: int, i: int) -> Path:
        return self.dir(deck_id) / "audio" / str(n) / f"s{i}.mp3"

    # meta -------------------------------------------------------------------
    def create(self, name: str) -> str:
        deck_id = self.new_id()
        self.dir(deck_id).mkdir(parents=True, exist_ok=True)
        self.write_meta(deck_id, {"deck_id": deck_id, "name": name, "status": "processing", "stage": "uploaded",
                                  "progress": 0.0, "error": None, "audio": {"done": 0, "total": 0}, "docs": []})
        return deck_id

    def meta(self, deck_id: str) -> dict:
        return read_json(self.dir(deck_id) / "meta.json", {}) or {}

    def write_meta(self, deck_id: str, meta: dict) -> None:
        write_json(self.dir(deck_id) / "meta.json", meta)

    def update_meta(self, deck_id: str, **fields) -> dict:
        m = self.meta(deck_id)
        m.update(fields)
        self.write_meta(deck_id, m)
        return m

    def list_ids(self) -> list[str]:
        out = []
        for p in self.settings.decks_dir.iterdir() if self.settings.decks_dir.exists() else []:
            if p.is_dir() and (p / "meta.json").exists():
                out.append(p.name)
        return sorted(out, key=lambda d: (self.dir(d) / "meta.json").stat().st_mtime, reverse=True)

    # content ----------------------------------------------------------------
    def slides(self, deck_id: str) -> list[dict]:
        return read_json(self.dir(deck_id) / "slides.json", []) or []

    def write_slides(self, deck_id: str, slides: list[dict]) -> None:
        write_json(self.dir(deck_id) / "slides.json", slides)

    def pdf_path(self, deck_id: str) -> Path:
        return self.dir(deck_id) / "slides.pdf"

    def layout(self, deck_id: str) -> dict:
        return read_json(self.dir(deck_id) / "layout.json", {}) or {}

    def write_layout(self, deck_id: str, layout: dict) -> None:
        write_json(self.dir(deck_id) / "layout.json", layout)

    def cursor_map(self, deck_id: str) -> dict:
        return read_json(self.dir(deck_id) / "cursor.json", {}) or {}

    def write_cursor_map(self, deck_id: str, m: dict) -> None:
        write_json(self.dir(deck_id) / "cursor.json", m)

    def narration(self, deck_id: str) -> list[dict]:
        return read_json(self.dir(deck_id) / "narration.json", []) or []

    def write_narration(self, deck_id: str, narration: list[dict]) -> None:
        write_json(self.dir(deck_id) / "narration.json", narration)
