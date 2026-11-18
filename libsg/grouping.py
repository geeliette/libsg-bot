"""Suggest which editions are "the same book".

Suggestions only: a Work exists once the user confirms one (see CLAUDE.md).

Starts from NLB's own grouping (SearchTitles' TitleSummary) and also merges
groups whose normalised title + author surname match, because NLB sometimes
splits one work (e.g. the 1981 "Emma" sits apart from the other "Emma." editions).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from libsg.models import Edition, SearchGroup

_ARTICLES = re.compile(r"^(the|a|an)\s+")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def title_key(title: str) -> str:
    """Main title, lowercased, no subtitle (after " : "), no leading article, no punctuation."""
    main = title.split(" : ")[0]
    return _ARTICLES.sub("", _fold(main))


def author_key(author: str) -> str:
    """Surname from "Austen, Jane, 1775-1817" style names; whole folded name otherwise."""
    surname = author.split(",")[0] if "," in author else author
    return _fold(surname)


def work_key(e: Edition) -> tuple[str, str]:
    return title_key(e.title), author_key(e.author)


@dataclass(frozen=True)
class SuggestedWork:
    title: str
    author: str
    editions: tuple[Edition, ...]

    @property
    def brns(self) -> tuple[int, ...]:
        return tuple(e.brn for e in self.editions)


def suggest_works(groups: list[SearchGroup], physical_only: bool = False) -> list[SuggestedWork]:
    """Merge NLB groups that share a work key. Keeps NLB's relevance order."""
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_with_key: dict[tuple[str, str], int] = {}
    for i, g in enumerate(groups):
        keys = {work_key(e) for e in g.editions} or {(title_key(g.title), author_key(g.author))}
        for k in keys:
            if k in first_with_key:
                a, b = find(first_with_key[k]), find(i)
                parent[max(a, b)] = min(a, b)
            else:
                first_with_key[k] = i

    merged: dict[int, list[int]] = {}
    for i in range(len(groups)):
        merged.setdefault(find(i), []).append(i)

    works = []
    for root in sorted(merged):
        seen: set[int] = set()
        eds: list[Edition] = []
        for i in merged[root]:
            for e in groups[i].editions:
                if e.brn in seen or (physical_only and not e.physical):
                    continue
                seen.add(e.brn)
                eds.append(e)
        if eds:
            head = groups[root]
            works.append(SuggestedWork(head.title, head.author, tuple(eds)))
    return works
