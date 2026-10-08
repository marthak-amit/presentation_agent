"""Safely set KEY=value in the project's .env (used by the in-app voice picker)."""
from __future__ import annotations

import os
import re
from pathlib import Path


def set_env_var(path: Path, key: str, value: str) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or re.search(r"[\r\n]", value):
        raise ValueError("bad env key/value")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    out, done = [], False
    for ln in lines:
        if pat.match(ln):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue  # drop duplicate definitions
        out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    os.environ[key] = value
