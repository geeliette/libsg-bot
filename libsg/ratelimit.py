"""Guards for on-demand "Check now".

Normal use should never notice these; they only stop button-spamming.

- Freshness: an edition checked in the last FRESH_FOR seconds (by anyone, or by
  the regular poll) isn't fetched again -- the saved result is shown. Pressing
  Check now repeatedly on one book therefore costs NLB nothing.
- Spam guard: more than `limit` presses within `window` seconds pauses that
  user's Check now for `cooldown` seconds. Kept in memory; a restart resets it.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque

FRESH_FOR = 2 * 60
VIEW_FRESH_FOR = 5 * 60  # opening a search result reuses checks this recent (no call, no spam-guard count)


class SpamGuard:
    def __init__(self, limit: int = 6, window: float = 60.0, cooldown: float = 300.0):
        self.limit = limit
        self.window = window
        self.cooldown = cooldown
        self._presses: dict[int, deque[float]] = defaultdict(deque)
        self._paused_until: dict[int, float] = {}

    def check(self, user_id: int, now: float | None = None) -> float:
        """Record a press. Returns 0 if allowed, else seconds left in the pause."""
        now = time.monotonic() if now is None else now
        until = self._paused_until.get(user_id, 0.0)
        if now < until:
            return until - now
        presses = self._presses[user_id]
        while presses and now - presses[0] >= self.window:
            presses.popleft()
        presses.append(now)
        if len(presses) > self.limit:
            presses.clear()
            self._paused_until[user_id] = now + self.cooldown
            return self.cooldown
        return 0.0

    @staticmethod
    def minutes(seconds: float) -> int:
        return max(1, math.ceil(seconds / 60))
