"""Typed views over raw NLB API responses (see docs/nlb-catalogue-openapi.json)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def clean_title(title: str | None) -> str:
    """NLB titles look like "Emma / Jane Austen ; edited by ..." -- drop the responsibility part.

    Non-Latin records come as "native | romanised" ("傲慢与偏见 = Pride and prejudice | Ao man yu ...");
    keep the native part, which usually includes the English title too.
    """
    title = (title or "").split(" | ")[0]
    return title.split(" / ")[0].strip().rstrip(".").strip() or "?"


def parse_year(publish_date: str | None) -> int | None:
    m = re.search(r"\d{4}", publish_date or "")
    return int(m.group(0)) if m else None


def _first(values: Any) -> str:
    if isinstance(values, list):
        return str(values[0]) if values else ""
    return str(values or "")


def clean_publisher(raw: str) -> str:
    """"Xinjiapo : Da zhong shu ju" -> "Da zhong shu ju"; "London : Penguin, 2003." -> "Penguin"."""
    name = re.split(r"\s*:\s*", raw.split(";")[0], maxsplit=1)[-1]  # drop "Place :" / "[Place]:"
    name = re.sub(r",?\s*(c|©)?\d{4}\.?$", "", name.strip())
    name = re.split(r",\s*an imprint of\b", name, flags=re.I)[0]
    name = name.strip(" ,.;:[]")
    return name if len(name) <= 30 else name[:29].rstrip() + "…"


def parse_pages(raw: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:pages|p\b)", raw or "")
    return int(m.group(1)) if m else None


def is_placeholder(rec: dict[str, Any]) -> bool:
    """NLB sometimes returns empty records with brn 0 and no data -- skip them."""
    return not rec.get("brn") or int(rec["brn"]) <= 0


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
    language: str = ""
    publisher: str = ""
    pages: int | None = None
    edition_note: str = ""

    @property
    def isbn(self) -> str:
        """First ISBN, digits only ("9781402256790 (electronic bk)" -> "9781402256790")."""
        for raw in self.isbns:
            m = re.match(r"[\dXx-]{10,17}", raw.strip())
            if m:
                return m.group(0).replace("-", "")
        return ""

    @property
    def catalogue_url(self) -> str:
        return f"https://catalogue.nlb.gov.sg/search/card?recordId={self.brn}"

    def describe(self) -> str:
        """Human label, e.g. "2025 · English · Penguin Books · 480 pages"."""
        bits = [str(self.year) if self.year else "Year unknown"]
        if self.language:
            bits.append(self.language)
        if self.publisher:
            bits.append(self.publisher)
        if self.edition_note:
            bits.append(self.edition_note)
        if self.pages:
            bits.append(f"{self.pages} pages")
        return " · ".join(bits)

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
            language=_first(rec.get("language")),
            publisher=clean_publisher(_first(rec.get("publisher"))),
            pages=parse_pages(_first(rec.get("physicalDescription"))),
            edition_note=_first(rec.get("edition")).strip().rstrip(".").strip(),
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
        eds = tuple(Edition.from_record(r, title, author) for r in t.get("records") or [] if not is_placeholder(r))
        groups.append(SearchGroup(clean_title(title), author or "", eds))
    return groups


def parse_titles(data: dict[str, Any]) -> list[Edition]:
    return [Edition.from_record(t) for t in data.get("titles") or [] if not is_placeholder(t)]


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
