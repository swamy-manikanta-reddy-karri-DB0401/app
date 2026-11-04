"""A small in-memory cache keyed by the caller's sign-in, for answers Databricks would give the same way
twice in a row (who you are, which assistants you may see).

Why (2026-11-04, sized for 20,000 people): every API request asked SCIM `/Me` who the caller was, and every
chat message rebuilt the caller's whole assistant list. Databricks allows 255 SCIM reads a minute per
workspace, so a busy morning would have been refused. Entries are keyed by a hash of the token (the token
itself is never kept), live a short time, and the size is bounded. A cache can never widen access: every
agent call still runs with the person's own token, and Databricks decides.
"""
from __future__ import annotations

import hashlib
import threading
import time


def key_of(tok: str) -> str:
    return hashlib.sha256((tok or "").encode("utf-8")).hexdigest()[:32]


class TTL:
    def __init__(self, seconds: float, size: int = 50_000):
        self.seconds, self.size = seconds, size
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        hit = self._d.get(key)
        if hit and time.monotonic() - hit[0] < self.seconds:
            return hit[1]
        return None

    def put(self, key: str, value) -> None:
        with self._lock:
            if len(self._d) >= self.size:
                # Drop the older half: cheap, and the next requests refill what is in use.
                for k in sorted(self._d, key=lambda k: self._d[k][0])[: self.size // 2]:
                    self._d.pop(k, None)
            self._d[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
