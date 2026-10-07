"""Persistent logs: per-session interruption log (JSONL) and the global unanswered-questions list."""
from __future__ import annotations

import json
import threading
from pathlib import Path


class LogStore:
    def __init__(self, logs_dir: Path):
        self.dir = logs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "sessions").mkdir(exist_ok=True)
        self._lock = threading.Lock()

    @property
    def unanswered_path(self) -> Path:
        return self.dir / "unanswered.json"

    def unanswered(self) -> list[dict]:
        try:
            return json.loads(self.unanswered_path.read_text())
        except (OSError, json.JSONDecodeError):
            return []

    def add_unanswered(self, entry: dict) -> None:
        with self._lock:
            items = self.unanswered()
            items.append(entry)
            tmp = self.unanswered_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2))
            tmp.replace(self.unanswered_path)

    def session_path(self, session_id: str) -> Path:
        return self.dir / "sessions" / f"{session_id}.jsonl"

    def add_interruption(self, session_id: str, entry: dict) -> None:
        with self._lock, self.session_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_session(self, session_id: str) -> list[dict]:
        p = self.session_path(session_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
