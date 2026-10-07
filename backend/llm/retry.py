"""Backoff helper for Groq free-tier rate limits (429)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger("llm.retry")
T = TypeVar("T")


def is_rate_limit(exc: BaseException) -> bool:
    if type(exc).__name__ == "RateLimitError":
        return True
    return getattr(exc, "status_code", None) == 429


def retry_after_s(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        v = headers.get("retry-after")
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    m = re.search(r"try again in ([\d.]+)s", str(exc))
    return float(m.group(1)) if m else None


async def with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 6,
    base: float = 2.0,
    cap: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Retry `fn` on 429 with exponential backoff (honouring Retry-After). Other errors propagate."""
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:
            if not is_rate_limit(e) or i == attempts - 1:
                raise
            wait = min(cap, max(retry_after_s(e) or 0, base * (2**i)))
            log.warning("429 from LLM, retry %d/%d in %.1fs", i + 1, attempts - 1, wait)
            await sleep(wait)
    raise RuntimeError("unreachable")
