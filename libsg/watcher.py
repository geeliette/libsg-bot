"""Decide which alerts to send when fresh availability arrives for an edition.

Rule (CLAUDE.md): alert only when an edition goes from not-available to
available at a watched branch. Concretely, for one BRN:

- First check ever (no snapshot): record a baseline, no alerts.
- A branch alerts when its borrowable count goes from 0 (or absent) to >= 1.
- 1 -> 2 does not alert; 2 -> 0 -> 1 alerts again.
- One alert per (user, BRN, branch), even if several of the user's watched
  works contain that BRN.
"""

from __future__ import annotations

from dataclasses import dataclass

from libsg.db import Store, WatchInfo
from libsg.models import Copy, available_by_branch


@dataclass(frozen=True)
class Alert:
    user_id: int
    watch_id: int
    work_id: int
    work_title: str
    brn: int
    branch_code: str
    branch_name: str
    available: int


def newly_available(prev: dict[str, int] | None, new: dict[str, int]) -> list[str]:
    """Branch codes that went from 0/absent to >= 1. Empty when prev is None (baseline)."""
    if prev is None:
        return []
    return sorted(b for b, n in new.items() if n > 0 and prev.get(b, 0) == 0)


def compute_alerts(
    brn: int,
    prev: dict[str, int] | None,
    new: dict[str, int],
    watches: list[WatchInfo],
    branch_names: dict[str, str] | None = None,
) -> list[Alert]:
    branch_names = branch_names or {}
    alerts: list[Alert] = []
    seen: set[tuple[int, str]] = set()
    for branch in newly_available(prev, new):
        for w in watches:
            if not w.covers(branch) or (w.user_id, branch) in seen:
                continue
            seen.add((w.user_id, branch))
            alerts.append(
                Alert(w.user_id, w.watch_id, w.work_id, w.work_title, brn, branch,
                      branch_names.get(branch, branch), new[branch])
            )
    return alerts


def process_edition(store: Store, brn: int, copies: list[Copy]) -> list[Alert]:
    """Compare fresh copies for one BRN against the stored snapshot, save the new one, return alerts."""
    new = available_by_branch(copies)
    names = {c.branch_code: c.branch_name for c in copies}
    alerts = compute_alerts(brn, store.get_snapshot(brn), new, store.watches_for_brn(brn), names)
    store.save_snapshot(brn, new)
    return alerts
