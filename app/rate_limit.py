from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Small in-memory burst limiter; it does not impose daily user quotas."""

    def __init__(self) -> None:
        self._events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: int, bucket: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        key = (user_id, bucket)
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:
                stale = [item for item, values in self._events.items() if not values or values[-1] <= cutoff]
                for item in stale[:1000]:
                    self._events.pop(item, None)
            return True
