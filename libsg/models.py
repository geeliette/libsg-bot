"""Typed views over raw NLB API responses (see docs/nlb-catalogue-openapi.json)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def clean_title(title: str | None) -> str:
    """NLB titles look like "Emma / Jane Austen ; edited by ..." -- drop the responsibility part."""
    return (title or "").split(" / ")[0].strip().rstrip(".").strip() or "?"


def parse_year(publish_date: str | None) -> int | None:
    m = re.search(r"\d{4}", publish_date or "")
    return int(m.group(0)) if m else None


@dataclass(frozen=True)
class Edition:
    """One BRN."""

    brn: int
    title: str
    author: str
    format_code: str
    format_name: str
    year: int | None
    isbns: tuple[str, ...] = ()
    physical: bool = True
    any_available: bool = False

    @classmethod
    def from_record(cls, rec: dict[str, Any], title: str | None = None, author: str | None = None) -> "Edition":
        """Build from a TitleRecord (SearchTitles, with parent title/author) or a Title (GetTitles)."""
        fmt = rec.get("format") or {}
        # Digital editions (Overdrive) carry a digitalId and have no branch copies.
        digital = bool(rec.get("digitalId")) or (rec.get("source") or "").lower() == "overdrive"
        return cls(
            brn=int(rec["brn"]),
            title=clean_title(title if title is not None else rec.get("title")),
            author=(author if author is not None else rec.get("author")) or "",
            format_code=str(fmt.get("code") or ""),
            format_name=str(fmt.get("name") or ""),
            year=parse_year(rec.get("publishDate")),
            isbns=tuple(rec.get("isbns") or ()),
            physical=not digital,
            any_available=bool(rec.get("availability")),
        )


@dataclass(frozen=True)
class SearchGroup:
    """A TitleSummary from SearchTitles: NLB's own grouping of editions."""

    title: str
    author: str
    editions: tuple[Edition, ...] = field(default_factory=tuple)


def parse_search(data: dict[str, Any]) -> list[SearchGroup]:
    groups = []
    for t in data.get("titles") or []:
        title, author = t.get("title"), t.get("author")
        eds = tuple(Edition.from_record(r, title, author) for r in t.get("records") or [])
        groups.append(SearchGroup(clean_title(title), author or "", eds))
    return groups


def parse_titles(data: dict[str, Any]) -> list[Edition]:
    return [Edition.from_record(t) for t in data.get("titles") or []]


@dataclass(frozen=True)
class Copy:
    """One physical item (from GetAvailabilityInfo)."""

    brn: int
    item_id: str
    branch_code: str
    branch_name: str
    status_code: str
    status_name: str
    call_number: str
    reference_only: bool
    due_date: str | None  # YYYY-MM-DD, only for copies on loan

    @classmethod
    def from_item(cls, it: dict[str, Any]) -> "Copy":
        loc = it.get("location") or {}
        status = it.get("status") or {}
        tx = it.get("transactionStatus") or {}
        code = status.get("code") or ""
        return cls(
            brn=int(it["brn"]),
            item_id=str(it.get("itemId") or it.get("irn") or ""),
            branch_code=loc.get("code") or "?",
            branch_name=loc.get("name") or "?",
            status_code=code,
            status_name=status.get("name") or "?",
            call_number=it.get("formattedCallNumber") or it.get("callNumber") or "",
            reference_only=bool(it.get("nonCirculatingFlag")),
            due_date=((tx.get("date") or "")[:10] or None) if code == "Out" else None,
        )

    @property
    def available(self) -> bool:
        # status.code seen in fixtures: "In" (On Shelf), "Out" (On Loan), "In-Transit".
        # Reference-only copies are also "In" but can't be borrowed.
        return self.status_code == "In" and not self.reference_only


def parse_copies(items: list[dict[str, Any]] | dict[str, Any]) -> list[Copy]:
    """Accepts a GetAvailabilityInfo response dict or a list of its items."""
    if isinstance(items, dict):
        items = items.get("items") or []
    return [Copy.from_item(i) for i in items]


def available_by_branch(copies: list[Copy]) -> dict[str, int]:
    """branch_code -> number of borrowable on-shelf copies (0 for branches with only unavailable copies)."""
    counts: dict[str, int] = {}
    for c in copies:
        counts[c.branch_code] = counts.get(c.branch_code, 0) + int(c.available)
    return counts
