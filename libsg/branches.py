"""Library branches users can watch, from NLB's Library API (GetBranches).

Source of truth is the API, not a hard-coded list: branches open and close
(renovations, relocations), and the API reflects that.

What counts:
- Lending libraries only: branchType "PL" (public) or "RL" (regional).
  Excludes the National Library reference floors ("NL") and "Other" (archives, etc.).
- Open = has an opening-hours schedule. NLB keeps closed branches as
  isActive=true but empties their `schedules` and explains in
  timing.closedDateRemarks (e.g. "closed for renovations").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

SINGAPORE = ZoneInfo("Asia/Singapore")
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

LENDING_TYPES = {"PL", "RL"}


@dataclass(frozen=True)
class Branch:
    code: str
    name: str
    short_name: str
    is_open: bool
    note: str = ""  # closure remark, plain text


def _plain(html: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_branches(data: dict[str, Any]) -> list[Branch]:
    out = []
    for b in data.get("branches") or []:
        if (b.get("branchType") or {}).get("code") not in LENDING_TYPES or not b.get("isActive", True):
            continue
        code = (b.get("branchCode") or "").upper()
        if not code:
            continue
        name = b.get("branchName") or code
        is_open = bool(b.get("schedules"))
        note = "" if is_open else _plain((b.get("timing") or {}).get("closedDateRemarks"))
        short = (b.get("shortName") or name).removesuffix(" Library")  # "Central Arts Library" -> "Central Arts"
        out.append(Branch(code, name, short, is_open, note))
    return sorted(out, key=lambda br: br.short_name.lower())


# --- opening hours ------------------------------------------------------------


@dataclass(frozen=True)
class Hours:
    """One library's opening hours. weekly: weekday (0 = Monday) -> (open, close).
    special: dated exceptions from closureSchedules -> (open, close), or None when closed all day."""

    weekly: dict[int, tuple[time, time]] = field(default_factory=dict)
    special: dict[date, tuple[time, time] | None] = field(default_factory=dict)

    def is_open(self, when: datetime) -> bool:
        when = when.astimezone(SINGAPORE)
        d = when.date()
        span = self.special[d] if d in self.special else self.weekly.get(d.weekday())
        return span is not None and span[0] <= when.time() < span[1]


def _time(s: str | None) -> time | None:
    try:
        return time.fromisoformat(s or "")
    except ValueError:
        return None


def parse_hours(data: dict[str, Any]) -> dict[str, Hours]:
    """Branch code -> Hours, for open lending libraries (same rules as parse_branches)."""
    open_codes = {b.code for b in parse_branches(data) if b.is_open}
    out = {}
    for b in data.get("branches") or []:
        code = (b.get("branchCode") or "").upper()
        if code not in open_codes:
            continue
        weekly = {}
        for s in b.get("schedules") or []:
            opens, closes = _time(s.get("openingTime")), _time(s.get("closingTime"))
            if s.get("day") in _DAYS and opens and closes and opens < closes:
                weekly[_DAYS.index(s["day"])] = (opens, closes)
        special: dict[date, tuple[time, time] | None] = {}
        for s in b.get("closureSchedules") or []:
            try:
                d = date.fromisoformat(s.get("date") or "")
            except ValueError:
                continue
            opens, closes = _time(s.get("openingTime")), _time(s.get("closingTime"))
            # Public holidays come as 00:00-00:00: closed all day. Shorter hours would come as a real span.
            special[d] = (opens, closes) if opens and closes and opens < closes else None
        if weekly:
            out[code] = Hours(weekly, special)
    return out


def any_open(hours: Mapping[str, Hours], when: datetime, grace: timedelta = timedelta(minutes=30)) -> bool:
    """True if some library is open now, or closed less than `grace` ago (books get shelved at closing)."""
    return any(h.is_open(when) or h.is_open(when - grace) for h in hours.values())
