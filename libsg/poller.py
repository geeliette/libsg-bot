"""One polling round: check every watched edition, return alerts to send.

Rate limiting (NLB returns 429 "API calls quota exceeded" after a few quick
calls; the quota isn't documented and no rate-limit headers are sent):

- Calls are spaced by `spacing` seconds.
- NLBClient already retries a 429 briefly. If it still fails, the round stops
  and the rest waits for the next round. store.watched_brns() returns the
  longest-unchecked editions first, so nothing is starved.
- A skipped or failed edition keeps its old snapshot: alerts are delayed,
  never invented or lost.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from libsg.db import Store
from libsg.models import parse_copies
from libsg.nlb_client import NLBError
from libsg.watcher import Alert, process_edition

log = logging.getLogger(__name__)

Fetch = Callable[[int], Awaitable[list[dict[str, Any]]]]
Sleep = Callable[[float], Awaitable[Any]]


@dataclass
class PollResult:
    alerts: dict[tuple[int, int], list[Alert]] = field(default_factory=dict)  # (user_id, work_id) -> alerts
    checked: int = 0
    failed: int = 0
    skipped: int = 0
    rate_limited: bool = False


def group_alerts(alerts: list[Alert]) -> dict[tuple[int, int], list[Alert]]:
    grouped: dict[tuple[int, int], list[Alert]] = defaultdict(list)
    for a in alerts:
        grouped[(a.user_id, a.work_id)].append(a)
    return dict(grouped)


async def check_editions(store: Store, brns: list[int], fetch: Fetch, sleep: Sleep = asyncio.sleep,
                         spacing: float = 10.0) -> PollResult:
    result = PollResult()
    collected: list[Alert] = []
    for i, brn in enumerate(brns):
        if i and spacing:
            await sleep(spacing)
        try:
            items = await fetch(brn)
        except NLBError as e:
            if e.status == 429:
                result.rate_limited = True
                result.skipped = len(brns) - i
                log.warning("Rate limited after %d calls; %d editions wait for next round", i, result.skipped)
                break
            result.failed += 1
            log.warning("BRN %s: %s", brn, e)
            continue
        except httpx.HTTPError as e:
            result.failed += 1
            log.warning("BRN %s: network error %s", brn, type(e).__name__)
            continue
        collected.extend(process_edition(store, brn, parse_copies(items)))
        result.checked += 1
    result.alerts = group_alerts(collected)
    return result


async def poll_once(store: Store, fetch: Fetch, sleep: Sleep = asyncio.sleep, spacing: float = 10.0) -> PollResult:
    return await check_editions(store, store.watched_brns(), fetch, sleep, spacing)
