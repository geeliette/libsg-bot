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
from dataclasses import dataclass
from typing import Any

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
