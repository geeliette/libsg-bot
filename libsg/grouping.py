"""Suggest which editions are "the same book".

Suggestions only: a Work exists once the user confirms one (see CLAUDE.md).

Uses NLB's own grouping (SearchTitles' TitleSummary) as is. We used to also merge
groups with the same title + author surname, but in every saved search that only
added other-language editions (e.g. the 1981 Indonesian "Emma" into the English
one), while NLB's groups were always one language.
"""

from __future__ import annotations

from dataclasses import dataclass

from libsg.models import Edition, SearchGroup


@dataclass(frozen=True)
class SuggestedWork:
    title: str
    author: str
    editions: tuple[Edition, ...]

    @property
    def brns(self) -> tuple[int, ...]:
        return tuple(e.brn for e in self.editions)


def suggest_works(groups: list[SearchGroup], physical_only: bool = False) -> list[SuggestedWork]:
    """One work per NLB group, in NLB's relevance order. Groups left with no editions are dropped."""
    works = []
    seen: set[int] = set()  # a BRN appears in one work only, even if NLB repeats it
    for g in groups:
        eds = []
        for e in g.editions:
            if e.brn in seen or (physical_only and not e.physical):
                continue
            seen.add(e.brn)
            eds.append(e)
        if eds:
            works.append(SuggestedWork(g.title, g.author, tuple(eds)))
    return works
